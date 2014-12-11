# $Id$
# Description:
# OSP Daemon core class.
#
# Authors:
# Hani Benhabiles <hani.benhabiles@greenbone.net>
#
# Copyright:
# Copyright (C) 2014 Greenbone Networks GmbH
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA.

""" OSP Daemon core class. """

# This is needed for older pythons as our current module is called the same
# as the package we are in ... Another solution would be to rename that file.
from __future__ import absolute_import

import logging
import socket
import ssl
import threading
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from ospd.misc import ScanCollection, ResultType

logger = logging.getLogger(__name__)

OSP_VERSION = "0.1.0"
OSPD_VERSION = "1.0+beta5"

def get_commands_table():
    """ Initializes the supported commands and their info. """

    return {'start_scan' : {'description' : 'Start a new scan.',
                            'attributes' : {'target' :
                                            'Target host to scan'},
                            'elements' : None},
            'help' : {'description' : 'Print the commands help.',
                      'attributes' :
                      {'format' : 'Help format. Could be text or xml.'},
                      'elements' : None},
            'get_scans' : {'description' : 'List the scans in buffer.',
                           'attributes' :
                           {'scan_id' : 'ID of a specific scan to get.',
                            'details' : 'Whether to return the full'\
                                        ' scan report.'},
                           'elements' : None},
            'delete_scan' : {'description' : 'Delete a finished scan.',
                             'attributes' :
                             {'scan_id' : 'ID of scan to delete.'},
                             'elements' : None},
            'get_version' : {'description' : 'Return various versions.',
                             'attributes' : None,
                             'elements' : None},
            'get_scanner_details' : {'description' :
                                     'Return scanner description and'\
                                     ' parameters',
                                     'attributes' : None,
                                     'elements' : None}}

def get_result_xml(result):
    """ Formats a scan result to XML format. """

    result_type = ResultType.get_str(result['type'])
    return '<result name="{0}" type="{1}" severity="{2}">{3}</result>'\
            .format(result['name'], result_type, result['severity'],
                    xml_escape(result['value']))

def simple_response_str(command, status, status_text, content=""):
    """ Creates an OSP response XML string.

    @param: OSP Command to respond to.
    @param: Status of the response.
    @param: Status text of the response.
    @param: Text part of the response XML element.

    @return: String of response in xml format.
    """
    assert command
    assert status
    assert status_text
    return '<{0}_response status="{1}" status_text="{2}">{3}'\
           '</{0}_response>'.format(command, status, status_text, content)


class OSPDError(Exception):
    """ This is an exception that will result in an error message to the
    client """
    def __init__(self, message, command='osp', status=400):
        self.message = message
        self.command = command
        self.status = status

    def asXML(self):
        return simple_response_str(self.command, self.status, self.message)


class OSPDaemon(object):
    """ Daemon class for OSP traffic handling.

    Every scanner wrapper should subclass it and make necessary additions and
    changes.
    * Add any needed parameters in __init__.
    * Implement check() method which verifies scanner availability and other
      environment related conditions.
    * Implement process_scan_params and exec_scan methods which are
      specific to handling the <start_scan> command, executing the wrapped
      scanner and storing the results.
    * Implement other methods that assert to False such as get_scanner_name,
      get_scanner_version.
    * Use Call set_command_attributes at init time to add scanner command
      specific options eg. the w3af profile for w3af wrapper.

    See OSPDw3af and OSPDOvaldi for wrappers examples.
    """

    def __init__(self, certfile, keyfile, cafile):
        """ Initializes the daemon's internal data. """
        # Generate certificate for default params with openvas-mkcert
        self.certs = dict()
        self.certs['cert_file'] = certfile
        self.certs['key_file'] = keyfile
        self.certs['ca_file'] = cafile
        self.scan_collection = ScanCollection()
        self.daemon_info = dict()
        self.daemon_info['name'] = "OSPd"
        self.daemon_info['version'] = OSPD_VERSION
        self.daemon_info['description'] = "No description"
        self.scanner_info = dict()
        self.scanner_info['name'] = 'No name'
        self.scanner_info['version'] = 'No version'
        self.scanner_info['description'] = 'No description'
        self.scanner_params = dict()
        self.server_version = None  # Set by the subclass.
        self.commands = get_commands_table()

    def set_command_attributes(self, name, attributes):
        """ Sets the xml attributes of a specified command. """
        if self.command_exists(name):
            command = self.commands.get(name)
            command['attributes'] = attributes

    def init_scanner_params(self, scanner_params):
        """ Initializes the scanner's parameters. """

        self.scanner_params = scanner_params
        command = self.commands.get('start_scan')
        command['elements']\
         = {'scanner_params' : {k : v['name'] for k, v in scanner_params.items()}}

    def command_exists(self, name):
        """ Checks if a commands exists. """
        return name in self.commands.keys()

    def get_scanner_name(self):
        """ Gives the wrapped scanner's name. """
        return self.scanner_info['name']

    def get_scanner_version(self):
        """ Gives the wrapped scanner's version. """
        return self.scanner_info['version']

    def get_scanner_description(self):
        """ Gives the wrapped scanner's description. """
        return self.scanner_info['description']

    def get_server_version(self):
        """ Gives the specific OSP server's version. """
        assert self.server_version
        return self.server_version

    def get_protocol_version(self):
        """ Gives the OSP's version. """
        return OSP_VERSION

    def process_scan_params(self, params):
        """ may be overriden by child """
        return params

    def handle_start_scan_command(self, scan_et):
        # Extract scan information
        target = scan_et.attrib.get('target')
        if target is None:
            raise OSPDError('No target attribute', 'start_scan')
        scanner_params = scan_et.find('scanner_params')
        if scanner_params is None:
            raise OSPDError('No scanner_params element', 'start_scan')
        params = {}
        for param in scanner_params:
            params[param.tag] = param.text

        # Set the default values
        for param in self.scanner_params:
            if param in params:
                continue
            params[param] = self.scanner_params[param].get('default', '')

        scan_id = self.create_scan(target, self.process_scan_params(params))

        self.start_scan(scan_id)
        text = '<id>{0}</id>'.format(scan_id)
        return simple_response_str('start_scan', 200, 'OK', text)


    def exec_scan(self, scan_id):
        """ Asserts to False. Should be implemented by subclass. """
        assert scan_id
        raise NotImplementedError

    def finish_scan(self, scan_id):
        """ Sets a scan as finished. """
        self.set_scan_progress(scan_id, 100)
        logger.info("{0}: Scan finished.".format(scan_id))

    def get_daemon_name(self):
        """ Gives osp daemon's name. """
        return self.daemon_info['name']

    def get_daemon_version(self):
        """ Gives osp daemon's version. """
        return self.daemon_info['version']

    def get_scanner_param_default(self, param):
        """ Returns default value of a scanner param. """
        assert type(param) is type(str())
        return self.scanner_params[param]['default']

    def get_scanner_params_xml(self):
        """ Returns the OSP Daemon's scanner params in xml format. """
        params_str = ""
        for param_id, param in self.scanner_params.items():
            param_str = "<scanner_param id='{0}' type='{1}'>"\
                        "<name>{2}</name><description>{3}</description>"\
                        "<default>{4}</default></scanner_param>"\
                         .format(param_id, param['type'], param['name'],
                                 param['description'], param['default'])
            params_str = ''.join([params_str, param_str])
        return "<scanner_params>{0}</scanner_params>".format(params_str)

    def bind_socket(self, address, port):
        """ Returns a socket bound on (address:port). """

        assert address
        assert port
        bindsocket = socket.socket()
        try:
            bindsocket.bind((address, port))
        except socket.error:
            logger.error("Couldn't bind socket on {0}:{1}"\
                         .format(address, port))
            return None

        logger.info('Now listening on %s:%s', address, port)
        bindsocket.listen(0)
        return bindsocket

    def new_client_stream(self, sock):
        """ Returns a new ssl client stream from bind_socket. """

        assert sock
        newsocket, fromaddr = sock.accept()
        logger.debug("New connection from"
                     " {0}:{1}".format(fromaddr[0], fromaddr[1]))
        try:
            ssl_socket = ssl.wrap_socket(newsocket, cert_reqs=ssl.CERT_REQUIRED,
                                         server_side=True,
                                         certfile=self.certs['cert_file'],
                                         keyfile=self.certs['key_file'],
                                         ca_certs=self.certs['ca_file'],
                                         ssl_version=ssl.PROTOCOL_TLSv1)
        except (ssl.SSLError, socket.error) as message:
            logger.error(message)
            return None
        return ssl_socket

    def handle_client_stream(self, stream):
        """ Handles stream of data received from client. """
        if stream is None:
            return
        data = ''
        stream.settimeout(2)
        while True:
            try:
                data = ''.join([data, stream.read(1024)])
                if len(data) == 0:
                    logger.warning("Empty client stream (Connection unexpectedly closed)")
                    return
            except (AttributeError, ValueError) as message:
                logger.error(message)
                return
            except ssl.SSLError as e:
                logger.debug('SSL error: %s', e)
                break
        if len(data) <= 0:
            logger.debug("Empty client stream")
            return
        try:
            response = self.handle_command(data)
        except OSPDError as e:
            response = e.asXML()
        stream.write(response)

    def close_client_stream(self, client_stream):
        """ Closes provided client stream """
        try:
            client_stream.shutdown(socket.SHUT_RDWR)
        except socket.error as msg:
            logger.debug(msg)
        client_stream.close()
        logger.debug('Connection to %s closed', client_stream)

    def start_daemon(self, address, port):
        """ Initialize the OSP daemon.

        @return True if success, False if error.
        """
        return self.bind_socket(address, port)

    def start_scan(self, scan_id):
        """ Starts the scan with scan_id. """

        logger.info("{0}: Scan started.".format(scan_id))
        t = threading.Thread(target=self.exec_scan, args=(scan_id, ))
        self.scan_collection.set_thread(scan_id, t)
        t.start()

    def handle_timeout(self, scan_id):
        """ Handles scanner reaching timeout error. """
        self.add_scan_error(scan_id, name="Timeout",
                            value="{0} exec timeout."\
                                   .format(self.get_scanner_name()))
        self.finish_scan(scan_id)

    def set_scan_progress(self, scan_id, progress):
        """ Sets scan_id scan's progress. """
        self.scan_collection.set_progress(scan_id, progress)

    def scan_exists(self, scan_id):
        """ Checks if a scan with ID scan_id is in collection.

        @return: 1 if scan exists, 0 otherwise.
        """
        return self.scan_collection.id_exists(scan_id)

    def handle_get_scans_command(self, scan_et):
        """ Handles <get_scans> command.

        @return: Response string for <get_scans> command.
        """

        details = True
        scan_id = scan_et.attrib.get('scan_id')
        details = scan_et.attrib.get('details')
        if details and details == '0':
            details = False

        response = ""
        if scan_id and scan_id in self.scan_collection.ids_iterator():
            self.check_scan_thread(scan_id)
            scan_str = self.get_scan_xml(scan_id, details)
            response = ''.join([response, scan_str])
        elif scan_id:
            text = "Failed to find scan '{0}'".format(scan_id)
            return simple_response_str('get_scans', 404, text)
        else:
            for scan_id in self.scan_collection.ids_iterator():
                self.check_scan_thread(scan_id)
                scan_str = self.get_scan_xml(scan_id, details)
                response = ''.join([response, scan_str])
        return simple_response_str('get_scans', 200, 'OK', response)

    def handle_help_command(self, scan_et):
        """ Handles <help> command.

        @return: Response string for <help> command.
        """
        help_format = scan_et.attrib.get('format')
        if help_format is None or help_format == "text":
            # Default help format is text.
            return simple_response_str('help', 200, 'OK',
                                       self.get_help_text())
        elif help_format == "xml":
            text = self.get_xml_str(self.commands)
            return simple_response_str('help', 200, 'OK', text)
        raise OSPDError('Bogus help format', 'help')

    def get_help_text(self):
        """ Returns the help output in plain text format."""

        txt = str('\n')
        for name, info in self.commands.iteritems():
            command_txt = "\t{0: <22} {1}\n".format(name, info['description'])
            if info['attributes']:
                command_txt = ''.join([command_txt, "\t Attributes:\n"])
                for attrname, attrdesc in info['attributes'].iteritems():
                    attr_txt = "\t  {0: <22} {1}\n".format(attrname, attrdesc)
                    command_txt = ''.join([command_txt, attr_txt])
            if info['elements']:
                command_txt = ''.join([command_txt, "\t Elements:\n",
                                       self.elements_as_text(info['elements'])])
            txt = ''.join([txt, command_txt])
        return txt

    def elements_as_text(self, elems, indent=2):
        """ Returns the elems dictionnary as formatted plain text. """
        assert elems
        text = ""
        for elename, eledesc in elems.iteritems():
            if type(eledesc) == type(dict()):
                desc_txt = self.elements_as_text(eledesc, indent + 2)
                desc_txt = ''.join(['\n', desc_txt])
            elif type(eledesc) == type(str()):
                desc_txt = ''.join([eledesc, '\n'])
            else:
                assert False, "Only string or dictionnary"
            ele_txt = "\t{0}{1: <22} {2}".format(' ' * indent, elename,
                                                 desc_txt)
            text = ''.join([text, ele_txt])
        return text

    def handle_delete_scan_command(self, scan_et):
        """ Handles <delete_scan> command.

        @return: Response string for <delete_scan> command.
        """
        scan_id = scan_et.attrib.get('scan_id')
        if scan_id is None:
            return simple_response_str('delete_scan', 404,
                                       'No scan_id attribute')

        if not self.scan_exists(scan_id):
            text = "Failed to find scan '{0}'".format(scan_id)
            return simple_response_str('delete_scan', 404, text)
        self.check_scan_thread(scan_id)
        if self.delete_scan(scan_id):
            return simple_response_str('delete_scan', 200, 'OK')
        raise OSPDError('Scan in progress', 'delete_scan')

    def delete_scan(self, scan_id):
        """ Deletes scan_id scan from collection.

        @return: 1 if scan deleted, 0 otherwise.
        """
        return self.scan_collection.delete_scan(scan_id)

    def get_scan_results_xml(self, scan_id):
        """ Gets scan_id scan's results in XML format.

        @return: String of scan results in xml.
        """
        results_str = str()
        for result in self.scan_collection.results_iterator(scan_id):
            result_str = get_result_xml(result)
            results_str = ''.join([results_str, result_str])
        return ''.join(['<results>', results_str, '</results>'])

    def get_xml_str(self, data):
        """ Creates a string in XML Format using the provided data structure.

        @param: Dictionnary of xml tags and their elements.

        @return: String of data in xml format.
        """

        response = str()
        for tag, value in data.items():
            if type(value) == type(dict()):
                value = self.get_xml_str(value)
            elif type(value) == type(list()):
                value = ', '.join([m for m in value])
            elif value is None:
                value = str()
            response = ''.join([response,
                                "<{0}>{1}</{2}>".format(tag, value,
                                                        tag.split()[0])])
        return response


    def get_scan_xml(self, scan_id, detailed=True):
        """ Gets scan in XML format.

        @return: String of scan in xml format.
        """
        if not scan_id:
            return self.get_xml_str({'scan': ''})

        target = self.get_scan_target(scan_id)
        progress = self.get_scan_progress(scan_id)
        start_time = self.get_scan_start_time(scan_id)
        end_time = self.get_scan_end_time(scan_id)
        if detailed is False:
            results_str = ""
        else:
            results_str = self.get_scan_results_xml(scan_id)

        return '<scan id="{0}" target="{1}" progress="{2}"'\
               ' start_time="{3}" end_time="{4}">{5}</scan>'\
                .format(scan_id, target, progress, start_time, end_time,
                        results_str)

    def handle_get_scanner_details(self):
        """ Handles <get_scanner_details> command.

        @return: Response string for <get_version> command.
        """
        description = self.get_scanner_description()
        scanner_params = self.get_scanner_params_xml()
        details = "<description>{0}</description>{1}".format(description,
                                                             scanner_params)
        return simple_response_str('get_scanner_details', 200, 'OK', details)

    def handle_get_version_command(self):
        """ Handles <get_version> command.

        @return: Response string for <get_version> command.
        """
        protocol = self.get_xml_str({'protocol' : {'name' : 'OSP',
                                                   'version' : OSP_VERSION}})

        daemon_name = self.get_daemon_name()
        daemon_ver = self.get_daemon_version()
        daemon = self.get_xml_str({'daemon' : {'name' : daemon_name,
                                               'version' : daemon_ver}})

        scanner_name = self.get_scanner_name()
        scanner_ver = self.get_scanner_version()
        scanner = self.get_xml_str({'scanner' : {'name' : scanner_name,
                                                 'version' : scanner_ver}})

        text = ''.join([protocol, daemon, scanner])
        return simple_response_str('get_version', 200, 'OK', text)

    def handle_command(self, command):
        """ Handles an osp command in a string.

        @return: OSP Response to command.
        """
        try:
            tree = ET.fromstring(command)
        except ET.ParseError:
            logger.debug("Erroneous client input: {0}".format(command))
            raise OSPDError('Invalid data')

        if not self.command_exists(tree.tag) and tree.tag != "authenticate":
            raise OSPDError('Bogus command name')

        if tree.tag == "get_version":
            return self.handle_get_version_command()
        elif tree.tag == "start_scan":
            return self.handle_start_scan_command(tree)
        elif tree.tag == "get_scans":
            return self.handle_get_scans_command(tree)
        elif tree.tag == "delete_scan":
            return self.handle_delete_scan_command(tree)
        elif tree.tag == "help":
            return self.handle_help_command(tree)
        elif tree.tag == "get_scanner_details":
            return self.handle_get_scanner_details()
        else:
            assert False, "Unhandled command: {0}".format(tree.tag)

    def check(self):
        """ Asserts to False. Should be implemented by subclass. """
        raise NotImplementedError

    def run(self, address, port):
        """ Starts the Daemon, handling commands until interrupted.

        @return False if error. Runs indefinitely otherwise.
        """
        sock = self.start_daemon(address, port)
        if sock is None:
            return False

        while True:
            client_stream = self.new_client_stream(sock)
            if client_stream is None:
                continue
            self.handle_client_stream(client_stream)
            self.close_client_stream(client_stream)

    def create_scan(self, target, options):
        """ Creates a new scan.

        @target: Target to scan.
        @options: Miscellaneous scan options.

        @return: New scan's ID.
        """
        return self.scan_collection.create_scan(target, options)

    def get_scan_options(self, scan_id):
        """ Gives a scan's list of options. """
        return self.scan_collection.get_options(scan_id)

    def set_scan_option(self, scan_id, name, value):
        """ Sets a scan's option to a provided value. """
        return self.scan_collection.set_option(scan_id, name, value)

    def check_scan_thread(self, scan_id):
        """ Check the scan's thread, and terminate the scan if not alive. """
        scan_thread = self.get_scan_thread(scan_id)
        progress = self.get_scan_progress(scan_id)
        if progress < 100 and not scan_thread.is_alive():
            self.set_scan_progress(scan_id, 100)
            self.add_scan_error(scan_id, "", "Scan thread failure.")
            logger.info("{0}: Scan terminated.".format(scan_id))

    def get_scan_thread(self, scan_id):
        """ Gives a scan's current exec thread. """
        return self.scan_collection.get_thread(scan_id)

    def get_scan_progress(self, scan_id):
        """ Gives a scan's current progress value. """
        return self.scan_collection.get_progress(scan_id)

    def get_scan_target(self, scan_id):
        """ Gives a scan's target. """
        return self.scan_collection.get_target(scan_id)

    def get_scan_start_time(self, scan_id):
        """ Gives a scan's start time. """
        return self.scan_collection.get_start_time(scan_id)

    def get_scan_end_time(self, scan_id):
        """ Gives a scan's end time. """
        return self.scan_collection.get_end_time(scan_id)

    def add_scan_log(self, scan_id, name="", value=""):
        """ Adds a log result to scan_id scan. """
        self.scan_collection.add_log(scan_id, name, value)

    def add_scan_error(self, scan_id, name="", value=""):
        """ Adds an error result to scan_id scan. """
        self.scan_collection.add_error(scan_id, name, value)

    def add_scan_alarm(self, scan_id, name='', value='', severity=''):
        """ Adds an alarm result to scan_id scan. """
        self.scan_collection.add_alarm(scan_id, name, value, severity)
