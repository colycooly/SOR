import sys
import re
import select
import socket
import time
from collections import deque

class RDP_socket:
    """
    Represents and manages an RDP socket
    Note: While not a formal class, "order tuples" are referenced throughout this documentation.
          They take the form (packet_type: string list, syn_num: int, pkt_len: int, ack_num: int, cur_window: int)
    
    Attributes
    ----------
    this_socket: socket.socket
        a UDP socket that represents the socket this class manages
    destination: socket.socket
        a UDP socket that represents the socket this class is sending data to
    sender_buffer: [string]
        a list of single-character strings representing the characters of message attempting to be sent
    syn_num: int
        an integer representing the next character to be sent as an index of sender_buffer
    recv_buffer: [string]
        a list of single-character strings representing the characters recieved but not yet processed
    len_output: int
        an integer counting the number of characters processed from recv_buffer
    output_buffer: (order tuple, string) collections.deque
        a deque recording orders and messages waiting to be sent
    sent_packets: {order tuple -> string}
        a dictionary containing all sent but unacknowledged order tuples as keys to their corresponding messages
    ack_num: int
        an integer representing the next character we are waiting to recieve as an index of the entire message we are recieving
    last_ack: int
        an integer representing the last acknowledgement number recieved
    ack_rep_num: int
        an integer representing the number of times the same acknowledgement number was recieved
    cur_window: int
        the current number of available indexes in recv_buffer
    max_window: int
        the total number of indexes in recv_buffer
    max_msg_len: int
        the maximum number of characters that can be transmitted within one packet
    keep_alive: bool
        a boolean representing if this program is seeking to terminate the connnection after it finishes its task
    sent_fin: int
        an integer representing the state of the sent "FIN" packet where 0 = unsent, 1 = sent, 2 = acknowledged
    recv_fin: bool
        a boolean representing whether a "FIN" packet has been recieved
    partner_window: int
        an integer representing how large the available space in destination's window is
    output_logs: bool
        a boolean representing whether or not sent/recieved package logs should be printed to standard output

    Methods
    -------
    set_keepalive(keep_alive: bool)
        sets the attribute keep_alive to the inputted value
    get_destination()
        returns the values of the attribute destination
    get_ending()
        returns a boolean representing whether the connection is completely closed or not
    send(more_to_send: string)
        splits the inputted string into characters and appends the new list to sender_buffer
    init_connect()
        initiates the connection to destination
    close_connect()
        begins the standard procedure to close the connection to destination
    init_rst()
        flushes all packets waiting to be sent, attempts to notify destination of the reset, and aborts the connection
    make_dat_pkt()
        generates a packet containing the next unsent part of sender_buffer and adds it to output_buffer
    react_order(read_order: order tuple, read_msg: string)
        processes and reacts to a given order tuple, potentially passing along the associated message
    react_rst()
        flushes all packets waiting to be sent and aborts the connection
    send_next()
        send the next packet waiting to be sent in output_buffer
    check_rep(recv_ack_num: int)
        returns a bool stating if recv_ack_num is the same as the previously recieved acknolwedgement number
    check_lost()
        determines if a packet is considered lost and resends said packet if it is
    parse_header(data: str)
        parses the raw data transmitted through the connection into an order tuple and a message
    get_data()
        returns data stored in recv_buffer after marking it as processed
    """

    def __init__(self, udp_socket, max_window, max_msg_len, destination, output_logs=False) -> None:
        """
        Constructor for RDP_Socket, initializing all attributes

        Arguments
        ---------
        udp_socket: socket.socket
        max_window: int
        max_msg_len: int
        destination: socket.socket
        output_logs=false: bool
        """
        self.this_socket = udp_socket
        self.destination = destination
        self.sender_buffer = []
        self.syn_num = 0
        self.recv_buffer = []
        self.len_output = 0
        self.output_buffer = deque()
        self.sent_packets = {}
        self.ack_num = 0
        self.last_ack = ()
        self.ack_rep_num = 0
        self.cur_window = max_window
        self.max_window = max_window
        for window_index in range(0, max_window):
            self.recv_buffer.append("")
        self.max_msg_len = max_msg_len
        self.keep_alive = True
        self.sent_fin = 0
        self.recv_fin = False
        self.partner_window = 0
        self.output_logs = output_logs

    def set_keepalive(self, keep_alive):
        """sets the attribute keep_alive to the inputted value"""
        self.keep_alive = keep_alive

    def get_destination(self):
        """returns the values of the attribute destination"""
        return self.destination

    def get_ending(self):
        """returns a boolean representing whether the connection is completely closed or not"""
        return self.sent_fin == 2 and self.recv_fin
    
    def send(self, more_to_send):
        """splits the inputted string into characters and appends each character to the sender_buffer attribute in order"""
        to_send_list = re.split("", more_to_send)
        to_send_list.pop(0)
        to_send_list.pop(len(to_send_list) - 1)
        self.sender_buffer.extend(to_send_list)

    def init_connect(self):
        """initiates the connection to destination by sending the synchronization packet"""
        syn_order = (["SYN", "ACK"], 0, 0, self.ack_num, self.cur_window)
        self.output_buffer.append((syn_order, ""))
        self.send_next()

    def close_connect(self):
        """initiates the safe closure of the connection to destination, ensuring that destination recieves the termination signal"""
        if self.sent_fin == 0:
            fin_order = (["FIN"], self.syn_num, 0, 0, 0)
            self.output_buffer.append((fin_order, ""))

    def init_rst(self):
        """flushes all packets waiting to be sent, attempts to notify destination of the reset, and aborts the connection"""
        try:
            while self.send_next() != (): #Flush everything waiting to be sent
                pass
        except:
            rst_order = (["RST"], self.syn_num, 0, self.ack_num, self.cur_window)
            self.output_buffer.append((rst_order, ""))
            self.send_next()
            self.sent_fin = 2
            self.recv_fin = True

    def make_dat_pkt(self):
        """generates a packet containing the next unsent part of sender_buffer and adds it to output_buffer"""
        if self.sent_fin == 0:
            to_send = ""
            len_pending_acks = 0
            for sent_order in self.sent_packets:
                len_pending_acks = len_pending_acks + sent_order[2]
            wdw_len = self.partner_window - len_pending_acks
            while wdw_len > 0 and self.syn_num < len(self.sender_buffer):
                data_left = len(self.sender_buffer) - self.syn_num + 1
                if data_left < self.max_msg_len and wdw_len >= data_left:
                    pkt_len = len(self.sender_buffer) - self.syn_num + 1
                elif wdw_len > self.max_msg_len:
                    pkt_len = self.max_msg_len
                else:
                    pkt_len = wdw_len
                for char_num in range(self.syn_num-1, self.syn_num - 1 + pkt_len):
                    to_send = to_send + self.sender_buffer[char_num]
                write_order = (["DAT"], self.syn_num, pkt_len, 0, 0)
                self.output_buffer.append((write_order, to_send))
                self.syn_num = self.syn_num + pkt_len
                wdw_len = wdw_len - pkt_len
                to_send = ""

    def react_order(self, read_order, read_msg):
        """processes and reacts to a given order tuple, potentially passing along the associated message"""
        if "RST" in read_order[0]:
            self.react_rst()
        elif "ACK" in read_order[0]:
            self.__react_ack(read_order[3], read_order[4])
        self.__check_overflow(read_msg)
        if "FIN" in read_order[0]:
                self.__react_fin(read_order, read_msg)
        if not self.recv_fin:
            if "DAT" in read_order[0]:
                self.__react_dat(read_order[1], read_order[2], read_msg)
            if "SYN" in read_order[0]:
                self.__react_syn(read_order[0])

    def __check_overflow(self, msg):
        """initiates a reset if msg would overflow the buffer"""
        if len(msg) > self.cur_window:
            self.init_rst()
    
    def react_rst(self):
        """flushes all packets waiting to be sent and aborts the connection"""
        try:
            while self.send_next() != ():
                pass
        except:
            self.sent_fin = 2
            self.recv_fin = True
            
    def __react_dat(self, msg_syn, msg_len, msg):
        """reads the received msg into recv_buffer, updating ack_num and cur_window as needed, and prepares an acknowledgement packet for sending"""
        if self.ack_num == msg_syn:
            next_index = (msg_syn - 1) % self.max_window
            data = re.split("", msg)
            data.pop(0)
            data.pop(len(data) - 1)
            for char in data:
                self.recv_buffer[next_index] = char
                next_index = (next_index + 1) % self.max_window
            self.ack_num = self.ack_num + msg_len
            self.cur_window = self.cur_window - len(data)
        if self.syn_num > 0:
            write_order = (["ACK"], 0, 0, self.ack_num, self.cur_window)
            self.output_buffer.append((write_order, ""))

    def __react_syn(self, commands):
        """updates ack_num to show the incoming side of the connection has been estabilshed"""
        self.ack_num = 1
        if "DAT" not in commands:
            write_order = (["ACK"], 0, 0, self.ack_num, self.max_window)
            self.output_buffer.append((write_order, ""))
    
    def __react_ack(self, ack_num, wdw_len):
        """checks if an incoming acknowledgement is a duplicate, and depending on the outcome updates corresponding attributes as needed"""
        is_duplicate = self.__check_rep(ack_num)
        self.last_ack = ack_num
        if not is_duplicate:
            if ack_num == len(self.sender_buffer) + 2:
                self.sent_fin = 2
            elif ack_num == len(self.sender_buffer) + 1 and not self.keep_alive:
                self.close_connect()
            elif self.syn_num == 0:
                self.syn_num = 1
            self.partner_window = wdw_len

    def __react_fin(self, order, msg):
        """prepares a response to a standard connection closing packet"""
        if "SYN" in order[0]:
            self.ack_num = 1
        if "DAT" in order[0]:
            self.__react_dat(order[1], order[2], msg)
            self.ack_num = self.ack_num + 1
        else:
            if not self.recv_fin:
                self.ack_num = self.ack_num + 1
            write_order = (["ACK"], 0, 0, self.ack_num, self.cur_window)
            self.output_buffer.append((write_order, ""))
        self.recv_fin = True

    def send_next(self) -> tuple:
        """
        formats and sends the next packet waiting to be sent in output_buffer, potentially merging several packets in output_buffer together 
        in advance if their information can be transmitted in one packet without losing anything
        """
        order = ()
        try:
            next_pkt_data = self.__generate_next_packet()
        except IndexError:
            pass
        else:
            order = next_pkt_data[0]
            data_str = next_pkt_data[1]
            to_send = "|".join(order[0]) + "\nSequence: " + str(order[1]) + "\nLength: " + str(order[2]) + "\nAcknowledgement: " + str(order[3]) + "\nWindow: " + str(order[4]) + "\n\n" + data_str
            if "SYN" in order[0] or "DAT" in order[0] or "FIN" in order[0]:
                formatted_order = (tuple(order[0]), order[1], order[2], order[3], order[4])
                self.sent_packets.update({formatted_order: (data_str, time.time())})
            if self.output_logs:
                self.__print_rdp_log("Send", order)
            packet = to_send.encode("UTF-8")
            self.this_socket.sendto(packet, self.destination)
            if "FIN" in order[0]:
                self.sent_fin = 1
        return order
    
    def __generate_next_packet(self):
        """handles other method invocations to allow merging packets together if no information loss would occur from doing so"""
        next_packet_orders = self.output_buffer.popleft()
        more_to_merge = True
        try:
            to_merge, more_to_merge = self.__should_merge_next(next_packet_orders)
            while more_to_merge:
                next_packet_orders = self.__merge_pkt_orders(next_packet_orders, to_merge)
                to_merge, more_to_merge = self.__should_merge_next(next_packet_orders)
        except IndexError:
            pass
        
        return next_packet_orders
                
    def __should_merge_next(self, merge_into):
        """determines whether two packets can be merged without information loss"""
        more_to_merge = True
        to_merge = self.output_buffer.popleft()
        merge_into_commands = merge_into[0][0]
        for command in to_merge[0][0]:
            if command in merge_into_commands:
                more_to_merge = False
                self.output_buffer.appendleft(to_merge)
                break
        return to_merge, more_to_merge

    def __merge_pkt_orders(self, base, merging) -> tuple:
        """merges two packets together"""
        next_order = base[0]
        to_merge_order = merging[0]
        next_order[0].extend(to_merge_order[0])
        if next_order[1] > to_merge_order[1]:
            merged_syn = next_order[1]
        else:
            merged_syn = to_merge_order[1]
        merged_len = next_order[2] + to_merge_order[2]
        if next_order[3] > to_merge_order[3]:
            merged_ack = next_order[3]
        else:
            merged_ack = to_merge_order[3]
        merged_window = self.cur_window
        merged_order = (next_order[0], merged_syn, merged_len, merged_ack, merged_window)
        merged_msg = base[1] + merging[1]
        return (merged_order, merged_msg)
    
    def check_rep(self, recv_ack_num) -> bool:
        """returns a bool stating if recv_ack_num is the same as the previously recieved acknolwedgement number"""
        is_duplicate = False
        try:
            if recv_ack_num == self.last_ack:
                is_duplicate = True
                self.ack_rep_num = self.ack_rep_num + 1
                if self.ack_rep_num > 2:
                    for sent_order in self.sent_packets:
                        if sent_order[1] == recv_ack_num and (sent_order, self.sent_packets.get(sent_order)[0]) not in self.output_buffer:
                            sent_order_list = (list(sent_order[0]), sent_order[1], sent_order[2], sent_order[3], sent_order[4])
                            self.output_buffer.appendleft((sent_order_list, self.sent_packets.get(sent_order)[0]))
        except IndexError:
            pass
        return is_duplicate

    def check_lost(self):
        """determines if a packet is considered lost and resends said packet if it is"""
        for order in self.sent_packets:
            order_data = self.sent_packets.get(order)
            order_list = (list(order[0]), order[1], order[2], order[3], order[4])
            if time.time() - order_data[1] > 1 and (order_list, order_data[0]) not in self.output_buffer:
                self.output_buffer.appendleft((order_list, order_data[0]))

    def parse_header(self, data: str):
        """parses the raw data transmitted through the connection into an order tuple and a message"""
        order = ()
        message = ""
        seq_num = -1
        msg_size = -1
        ack_num = -1
        wdw_size = -1
        data_tuple = data.partition("\n\n")
        header_args = re.split("\n", data_tuple[0])
        commands = re.findall("SYN|ACK|DAT|FIN|RST", header_args.pop(0))
        message = data_tuple[2]
        for arg in header_args:
            if "RST" in commands:
                self.react_rst()
            split_header = re.split(":\s", arg)
            if split_header[0] == "Sequence":
                seq_num = int(split_header[1])
            elif split_header[0] == "Length":
                msg_size = int(split_header[1])
            elif split_header[0] == "Acknowledgement":
                ack_num = int(split_header[1])
                sent_packets_full = self.sent_packets.copy()
                for sent_pkt_order in sent_packets_full:
                    if ack_num > sent_pkt_order[1]:
                        self.sent_packets.pop(sent_pkt_order)
            elif split_header[0] == "Window":
                wdw_size = int(split_header[1])
            else:
                pass
        order = (commands, seq_num, msg_size, ack_num, wdw_size)  
        if self.output_logs:  
            self.__print_rdp_log("Recieve", order)
        return order, message

    def __print_rdp_log(self, io_direction, order) -> None:
        """formulates and prints a log statement in human-readable format about an inputted order and direction"""
        log_line = time.asctime(time.localtime(time.time()))
        log_line = log_line.replace("2022", "PST 2022: ")
        log_line = log_line + " " + io_direction + "; "
        log_line = log_line + "|".join(order[0]) + ";"
        log_line = log_line + " Sequence: " + str(order[1]) + "; "
        log_line = log_line + "Length: " + str(order[2]) + "; "
        log_line = log_line + "Acknowledgement: " + str(order[3]) + "; "
        log_line = log_line + "Window: " + str(order[4])
        print(log_line)
    
    def get_data(self, len_to_give) -> str:
        """returns data stored in recv_buffer after setting the corresponding attrbutes to indicate the buffer space it previously occupied is now available"""
        data = ""
        start_index = self.len_output % self.max_window
        if self.cur_window == self.max_window:
            return data
        elif len_to_give > (self.max_window - self.cur_window):
            len_to_give = self.max_window - self.cur_window
        try:
            for raw_index in range(start_index, start_index + len_to_give):
                index = raw_index % self.max_window
                char = self.recv_buffer[index]
                data = data + char
        except IndexError:
            pass
        if data != "":
            self.len_output = self.len_output + len(data)
            self.cur_window = self.cur_window + len(data)
        return data

def main():
    """sets up the prerequisite variables and objects, then polls for incoming connection requests and manages existing ones"""
    read_ports = []
    connections = {}
    timeout_dict = {}
    server_port = initialize_server()
    read_ports.append(server_port)
    data_fragment = ""
    while True:
        read_from, write_from, got_exception = select.select(read_ports, read_ports, read_ports, 5)
        for client in connections:
            client_rdp = connections.get(client)
            is_out = check_timeout(client, timeout_dict) or client_rdp.get_ending()
            if is_out:
                close_socket(client_rdp, connections, timeout_dict)
            else:
                client_rdp.check_lost()
        for to_read in read_from:
            recieve_data(to_read, connections, timeout_dict, data_fragment)
        for to_write in write_from:
            for client in connections:
                connections.get(client).make_dat_pkt()
                sent = connections.get(client).send_next()
                if sent != ():
                    timeout_dict[client] = time.time()
        for got_err in got_exception:
            close_socket(got_err, connections, timeout_dict)          

def initialize_server():
    """parses program arguments to create the server's socket"""
    ip_addr = sys.argv[1]
    port_num = int(sys.argv[2])
    io_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    io_socket.bind((ip_addr, port_num))
    return io_socket

def check_timeout(client, timeout):
    """checks if a connection has timed out (>= 30 seconds)"""
    if ((time.time() - timeout.get(client)) >= 30):
        return True
    else:
        return False

def recieve_data(server_port, connections, timeout, prev_fragment):
    """manages the recieving of data through io_socket, including seperating and parsing any headers from the message proper"""
    next_fragment = ""
    try:
        server_port.setblocking(False)
        data, sender = server_port.recvfrom(int(sys.argv[4])+100)
    except Exception:
        pass
    else:
        if sender not in connections:
            sender_rdp = RDP_socket(server_port, int(sys.argv[3]), int(sys.argv[4]), sender)
            connections.update({sender: sender_rdp})
            sender_rdp.init_connect()
        else:
            sender_rdp = connections.get(sender)
        timeout[sender] = time.time()
        data_str = data.decode("UTF-8")
        data_list = re.split("\n", data_str)
        data_str = prev_fragment + data_str
        data_list = re.findall("(Sequence: \d+\nLength: \d+\nAcknowledgement: \d+\nWindow: \d+\n\n)", data_str)
        seperator = data_list[0]
        partitioned_str = data_str.partition(seperator)
        message_len = int(re.findall("Length: (\d+)", seperator)[0])
        if message_len != 0:
            seperator = partitioned_str[2][0:message_len]
            partitioned_str = data_str.partition(seperator)
        next_fragment = partitioned_str[2]
        data_str = partitioned_str[0] + partitioned_str[1]
        order, message = sender_rdp.parse_header(data_str)
        if order != ():
            sender_rdp.react_order(order, message)
        data_str = sender_rdp.get_data(int(sys.argv[4])+100)
        request_list = re.findall("GET /[\w\.\-]+ HTTP\/1\.0(?:\r\nConnection:\s?[Kk]eep-alive)?\r\n", data_str)
        for request in request_list:
            found_err_str = check_format(request, sender_rdp)
            if found_err_str != None:
                print(found_err_str)
                sender_rdp.send(found_err_str)
                sender_rdp.set_keepalive(False)
                break
            else:
                process_request(request, sender_rdp)
        return next_fragment

#Note: this function is incomplete and unused
def split_multiple_requests(requests_str):
"""splits multiple simultainious requests into individually parsable single requests"""
    requests_list = []
    this_request = []
    request = ""
    split_request = split_input(requests_str)
    for element in split_request:
        if element != "":
            this_request.append(element)
        elif this_request != []:
            request = " ".join(this_request)
            request = request + "\n"
            if re.match("Connection:", request):
                request = requests_list[len(requests_list) - 1] + request
            requests_list.append(request)
            this_request = []
    return requests_list

def create_timestamp(client_connection):
    """creates a formatted timestamp"""
    timestamp = time.asctime(time.localtime(time.time()))
    timestamp = timestamp.replace("2022", "PST 2022") #PST is hardcoded, but timestamp is local time
    timestamp = timestamp + " " + client_connection[0] 
    timestamp = timestamp + ":" + str(client_connection[1])
    return timestamp

def split_input(input: str):
    """splits a recieved request line by the pieces of data it communicates"""
    input_list = re.split("\s", input)
    try:
        if input_list[3] == "Connection:":
            tail = input_list.pop(4)
            input_list[3] = input_list[3] + tail
    except IndexError:
        pass
    return input_list

def check_format(request_str: str, client):
    """verifies the formatting on incoming HTTP requests"""
    format_err = create_timestamp(client.get_destination()) + " HTTP/1.0 400 Bad Request\r\n\r\n"
    last_index = 4
    request = re.split("\s", request_str)
    try:
        if not (request[0] == "GET" and request[2] == "HTTP/1.0"):
            return format_err
        try:
            if re.search("Connection:.*", request[4]):
                last_index = 7
        except IndexError:
            pass
        if request[last_index] != "" or request[last_index-1] != "":
            return format_err   
    except IndexError:
        return format_err
    try:
        request[last_index+1]
    except IndexError:
        return None
    else:
        return format_err

def process_request(request: str, client_rdp):
    """parses a recieved HTTP request"""
    response = "HTTP/1.0 200 OK"
    header_info = ""
    file_data = ""
    header_ending = "\r\n"
    request_list = split_input(request)
    if re.search("\nConnection:\s?[Kk]eep-alive", request_list[3]) != None:
        client_rdp.set_keepalive(True)
    else:
        client_rdp.set_keepalive(False)
    try:
        file_handle = open("." + request_list[1], "r")
        file_data = file_handle.read()
        file_handle.close()
        header_info = header_info + "Content-Length: " + str(len(file_data)) + "\r\n"
    except FileNotFoundError:
        response = "HTTP/1.0 404 Not Found"
    request_log = " " + request_list[0] + " " + request_list[1] + " " + request_list[2]
    print(create_timestamp(client_rdp.get_destination()) + request_log + "; " + response)
    client_rdp.send(response + "\r\n" + header_info + header_ending + file_data)

def close_socket(rdp, connections, timeout):
    """closes a socket while removing now unneccesary pieces of information"""
    if rdp in connections:
        connections.remove(rdp)
    if rdp in timeout:
        timeout.pop(rdp)
    del rdp

if __name__ == "__main__":
    main()