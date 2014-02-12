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
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2,
# or, at your option, any later version as published by the Free
# Software Foundation
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA.

import socket
import ssl
import thread
import xml.etree.ElementTree as ET
from misc import ScanCollection, OSPLogger

OSP_VERSION = "0.0.1"

class OSPDaemon(object):
    """ Daemon class for OSP traffic handling.

    Every scanner wrapper should subclass it and make necessary additions and
    changes.
    * Add any needed parameters in __init__.
    * Implement check() method which verifies scanner availability and other
      environment related conditions.
    * Implement handle_start_scan_command and exec_scanner methods which are
      specific to handling the <start_scan> command, executing the wrapped
      scanner and storing the results.
    * Implement other methods that assert to False such as get_scanner_name,
      get_scanner_version.
    * Use Call set_command_attributes at init time to add scanner command
      specific options eg. the w3af profile for w3af wrapper.

    See OSPDw3af and OSPDOvaldi for wrappers examples.
    """

    def __init__(self, certfile, keyfile, timeout, debug, port, address):
        """ Initializes the daemon's internal data. """
        # Generate certificate for default params:
        # openssl req -new -x509 -days 365 -nodes -out cert.pem -keyout cert.pem
        self.cert_file = certfile
        self.key_file = keyfile
        self.port = port
        self.timeout = timeout
        self.scan_collection = ScanCollection()
        self.logger = OSPLogger(debug)
        self.address = address
        self.name = "generic ospd"
        self.version = "generic version"
        self.commands = self.get_commands_table()

    def get_commands_table(self):
        """ Initializes the supported commands and their info. """

        return {'start_scan' : { 'description' : 'Start a new scan.',
                                 'attributes' :
                                   { 'target' : 'Target host to scan'},
                                 'elements' : None },
                'help' : { 'description' : 'Print the commands help.',
                           'attributes' : None,
                           'elements' : None },
                'get_scans' : { 'description' : 'List the scans in buffer.',
                                 'attributes' : None,
                                 'elements' : None },
                'delete_scan' : { 'description' : 'Delete a finished/stopped scan.',
                                  'attributes' :
                                   { 'id' : 'Scan ID.'},
                                  'elements' : None },
                'get_version' : { 'description' : 'Return various versions.',
                                  'attributes' : None,
                                  'elements' : None }}

    def set_command_attributes(self, name, attributes):
        """ Sets the xml attributes of a specified command. """
        if self.command_exists(name):
            command = self.commands.get(name)
            command['attributes'] = attributes

    def set_command_elements(self, name, elements):
        """ Sets the xml subelements of a specified command. """
        if self.command_exists(name):
            command = self.commands.get(name)
            command['elements'] = elements

    def command_exists(self, name):
        """ Checks if a commands exists. """
        return name in self.commands.keys()

    def get_scanner_name(self):
        """ Asserts to False. Should be implemented by subclass. """
        assert False, 'get_scanner_name() not implemented.'

    def get_scanner_version(self):
        """ Asserts to False. Should be implemented by subclass. """
        assert False, 'get_scanner_version() not implemented.'

    def handle_start_scan_command(self):
        """ Asserts to False. Should be implemented by subclass. """
        assert False, 'handle_start_scan_command() not implemented.'

    def exec_scanner(self):
        """ Asserts to False. Should be implemented by subclass. """
        assert False, 'exec_scanner() not implemented.'

    def get_daemon_name(self):
        """ Gives osp daemon's name. """
        return self.name

    def get_daemon_version(self):
        """ Gives osp daemon's version. """
        return self.version

    def bind_socket(self):
        """ Returns a socket bound on (address:port). """
        bindsocket = socket.socket()
        try:
            bindsocket.bind((self.address, self.port))
        except socket.error, e:
            self.logger.error("Couldn't bind socket on {0}:{1}"
                               .format(self.address, self.port))
            return None

        bindsocket.listen(0)
        return bindsocket

    def new_client_stream(self):
        """ Returns a new ssl client stream from bind_socket. """

        newsocket, fromaddr = self.socket.accept()
        try:
            ssl_socket = ssl.wrap_socket(newsocket,
                                         server_side=True,
                                         certfile=self.cert_file,
                                         keyfile=self.key_file,
                                         ssl_version=ssl.PROTOCOL_TLSv1)
        except ssl.SSLError as err:
            self.logger.error("Null client stream.")
            return None
        return ssl_socket

    def handle_client_stream(self, stream):
        """ Handles stream of data received from client. """
        if stream is None:
            return
        while True:
            try:
                data = stream.read()
            except AttributeError:
                self.logger.debug(1, "Couldn't read client input.")
                return

            if len(data) <= 0:
                return

            response = self.handle_command(data)
            stream.write(response)

    def close_client_stream(self, client_stream):
        """ Closes provided client stream """
        try:
            client_stream.shutdown(socket.SHUT_RDWR)
        except socket.error, msg:
            self.logger.debug(1, msg)
        client_stream.close()

    def start_daemon(self):
        """ Initialize the OSP daemon.

        @return 1 if error, 0 otherwise.
        """
        self.socket = self.bind_socket()
        if self.socket is None:
            return 1
        return 0

    def start_scan(self, scan_id):
        """ Starts the scan with scan_id. """
        thread.start_new_thread (self.exec_scanner, (scan_id, ))

    def handle_timeout(self, scan_id):
        """ Handles scanner reaching timeout error. """
        self.add_scan_error(scan_id, "{0} reached exec timeout.".format(self.get_scanner_name()))
        self.set_scan_progress(scan_id, 100)

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
        response = '<get_scans_response status="200" satus_text="OK">'
        for scan_id in self.scan_collection.ids_iterator():
            scan_str = self.get_scan_xml(scan_id)
            response = ''.join([response, scan_str])
        response = ''.join([response, '</get_scans_response>'])
        return response

    def handle_help_command(self, scan_et):
        """ Handles <help> command.

        @return: Response string for <help> command.
        """
        help_format = scan_et.attrib.get('format')
        if help_format is None:
            # Default help format is text.
            return self.create_response_string({'help_response status="200" '
                                                'status_text="OK"' :
                                                 self.get_help_text()})
        elif help_format == "xml":
            return self.create_response_string({'help_response status="200" '
                                                'status_text="OK"' :
                                                 self.commands})
        else:
            return "<help_response status='400' status_text='Bogus help format'/>"

    def get_help_text(self):
        """ Returns the help output in plain text format."""

        txt = str('\n')
        for name, info in self.commands.iteritems():
            command_txt = "\t{0: <10}\t\t{1}\n".format(name, info['description'])
            if info['attributes']:
                command_txt = ''.join([command_txt, "\t Attributes:\n"])
                for attrname, attrdesc in info['attributes'].iteritems():
                    attr_txt = "\t  {0: <10}\t\t {1}\n".format(attrname, attrdesc)
                    command_txt = ''.join([command_txt, attr_txt])
            if info['elements']:
                command_txt = ''.join([command_txt, "\t Elements:\n"])
                for elename, eledesc in info['elements'].iteritems():
                    ele_txt = "\t  {0: <10}\t\t {1}\n".format(elename, eledesc)
                    command_txt = ''.join([command_txt, ele_txt])
            txt = ''.join([txt, command_txt])

        return txt

    def handle_delete_scan_command(self, scan_et):
        """ Handles <delete_scan> command.

        @return: Response string for <delete_scan> command.
        """
        scan_id = scan_et.attrib.get('id')
        if scan_id is None:
            return "<delete_scan status='400' status_text='No id attribute'/>"

        if not self.scan_exists(scan_id):
            return self.create_response_string({'delete_scan_response'
                                                ' status="404"'
                                                ' status_text="Not Found"' :
                                                'Scan {0} not found'.format(scan_id)})
        if self.delete_scan(scan_id):
            return self.create_response_string({'delete_scan_response'
                                                ' status="200"'
                                                ' status_text="OK"' : ''})

        return "<delete_scan status='400' status_text='Scan in progress'/>"

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
            results_str = ''.join([results_str, '<result type="{0}">'.format(result[0]),
                                   str(result[1]), '</result>'])
        return ''.join(['<results>', results_str, '</results>'])

    def create_response_string(self, data):
        """ Creates a string in XML Format using the provided data structure.

        @param: Dictionnary of xml tags and their elements.

        @return: String of data in xml format.
        """

        response = str()
        for tag, value in data.items():
            if type(value) == type(dict()):
                value = self.create_response_string(value)
            elif type(value) == type(list()):
                value = ', '.join([m for m in value])
            elif value is None:
                value = str()
            response = ''.join([response,
                                "<{0}>{1}</{2}>".format(tag, value,
                                                        tag.split()[0])])

        return response

    def get_scan_xml(self, scan_id):
        """ Gets scan in XML format.

        @return: String of scan in xml format.
        """
        if not scan_id:
            return self.create_response_string({'scan': ''})

        target = self.get_scan_target(scan_id)
        progress = self.get_scan_progress(scan_id)
        results_str = self.get_scan_results_xml(scan_id)
        options = self.get_scan_options(scan_id)
        options_str = self.create_response_string({'options' : options})

        return '<scan id="{0}" target="{1}" progress="{2}">{3}{4}</scan>'\
                .format(scan_id, target, progress, options_str, results_str)

    def handle_get_version_command(self, get_version_et):
        """ Handles <get_version> command.

        @return: Response string for <get_version> command.
        """
        protocol = self.create_response_string({'protocol' :
                                                {'name' : 'OSP',
                                                 'version' : OSP_VERSION}})

        daemon_name = self.get_daemon_name()
        daemon_ver = self.get_daemon_version()
        daemon = self.create_response_string({'daemon' :
                                              {'name' : daemon_name,
                                               'version' : daemon_ver}})

        scanner_name = self.get_scanner_name()
        scanner_ver = self.get_scanner_version()
        scanner = self.create_response_string({'scanner' :
                                               {'name' : scanner_name,
                                                'version' : scanner_ver}})

        return ''.join(["<get_version_response status='200' status_text='OK'>",
                        protocol, daemon, scanner, "</get_version_response>"])

    def handle_command(self, command):
        """ Handles an osp command in a string.

        @return: OSP Response to command.
        """
        try:
            tree = ET.fromstring(command)
        except ET.ParseError, e:
            self.logger.debug(1, "Couldn't parse erroneous client input.")
            return "<osp_response status='400' status_text='Invalid data'/>"

        if not self.command_exists(tree.tag) and tree.tag != "authenticate":
            return "<osp_response status='400' status_text='Bogus command name'/>"

        if tree.tag == "authenticate":
            # OpenBar! status 200 as checked by omp-cli XXX
            return "<authenticate_response status='200' status_text='OK'/>"
        elif tree.tag == "get_version":
            return self.handle_get_version_command(tree)
        elif tree.tag == "start_scan":
            return self.handle_start_scan_command(tree)
        elif tree.tag == "get_scans":
            return self.handle_get_scans_command(tree)
        elif tree.tag == "delete_scan":
            return self.handle_delete_scan_command(tree)
        elif tree.tag == "help":
            return self.handle_help_command(tree)
        else:
            assert False, "Unhandled command: {0}".format(tree.tag)

    def check(self):
        """ Asserts to False. Should be implemented by subclass. """
        assert False, 'check() not implemented.'

    def run(self):
        """ Starts the Daemon, handling commands until interrupted.

        @return 1 if error, 0 otherwise.
        """
        if self.start_daemon():
            return 1

        while True:
            client_stream = self.new_client_stream()
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

    def get_scan_progress(self, scan_id):
        """ Gives a scan's current progress value. """
        return self.scan_collection.get_progress(scan_id)

    def get_scan_target(self, scan_id):
        """ Gives a scan's target. """
        return self.scan_collection.get_target(scan_id)

    def add_scan_log(self, scan_id, message):
        """ Adds a log result to scan_id scan. """
        self.scan_collection.add_log(scan_id, message)

    def add_scan_error(self, scan_id, message):
        """ Adds an error result to scan_id scan. """
        self.scan_collection.add_error(scan_id, message)

    def add_scan_alert(self, scan_id, message):
        """ Adds an alert result to scan_id scan. """
        self.scan_collection.add_alert(scan_id, message)
