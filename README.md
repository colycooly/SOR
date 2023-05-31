# SOR
SOR, short for "Server over Reliable Datagram Protocol" is a simple project that allows any number of clients (systems running sor-client) to connect simultainiously, consecutively, or randomly to a constantly active server (system running sor-server), using basic HTTP requests to copy and recieve simple text files.

## Functionality
Any number of different clients (typically but not neccesarily) running sor-client.py may request text files from an active server (typically but not neccesarily) running sor-server.py. Files requested by the client are, if found in the server's directory, copied and sent over an RDP connection estabilshed by the program to the client. After all requested files are confirmed to have been transmitted, the client may send more requests or disconnect. sor-client.py always disconnects after all initially requested files have finished or failed transmission.

## Usage
Depending on operating system, to run each end of the program on the corresponding system invoke the file using

For Windows:  

  py sor-server.py server_ip_address server_port_number server_buffer_size server_payload_length  
  py sor-client.py server_ip_address server_port_number client_buffer_size client_payload_length read_file_name0 write_file_name0 *read_file_name1 write_file_name1 ...*

For Linux: 

  python3 sor-server.py server_ip_address server_port_number server_buffer_size server_payload_length    
  python3 sor-client.py server_ip_address server_port_number client_buffer_size client_payload_length read_file_name0 write_file_name0 *read_file_name1 write_file_name1 ...*

All arguments in italics are optional, though for every read_file_nameX there must be a corresponding write_file_nameX  
Note that server_ip_address and server_port_number are always the local ip address and port number when used on the server, but could be the router's ip address and port number if the connection is not within LAN.

## Program Details
### Connection Codes
The full list of valid connection codes are all follows:
1. "SYN" (synchronize)
2. "ACK" (acknowledgment)
3. "DAT" (data)
4. "FIN" (finish)
5. "RST" (reset)

## Miscellaneous Notes
- Does not support multibyte characters. Transmitting some files with multibyte chatacters will work, but it will not work for some files.
- Only supports documents that are composed entirely of ascii characters
- Neither the client nor the server are designed to support interfacing with other protocols in any capacity.
