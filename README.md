# BitTorrent File Sharing System

A simplified implementation of the BitTorrent protocol for distributed file sharing.

## Overview

This project implements a lightweight BitTorrent-like system with four main components:
- **Torrent Creator**: Creates .torrent metadata files and uploads them to Dropbox
- **Tracker**: Coordinates peers and maintains peer lists
- **Seeder**: Shares complete files with others
- **Leecher**: Downloads files from seeders, then can become a seeder

## Features

- File sharing using 512KB pieces
- Centralized tracker for peer discovery
- File integrity verification using SHA1 hashing
- Multi-threaded downloads for faster transfer speeds
- Automatic transition from leecher to seeder after successful download
- Dropbox integration for .torrent file storage and distribution
- Detailed tracker monitoring interface
- Proper peer cleanup when clients exit

## How It Works Across Multiple PCs

This BitTorrent system is designed to work seamlessly across multiple computers on the same network. Here's how it functions:

### Network Architecture

1. **Central Tracker**: One computer runs the tracker, which serves as the coordination point.
2. **Distributed Peers**: Multiple computers can act as seeders or leechers within the same network.
3. **Direct P2P Communication**: After discovery, peers communicate directly with each other without going through the tracker.

### Technical Mechanism

The system uses the following mechanism to enable multi-PC file sharing:

1. **IP-based Peer Registration**: 
   - When a peer starts, it registers with the tracker using its IP address and port
   - The tracker stores this information in a dictionary mapping torrent names to sets of peer addresses (`peers = {torrent_name -> set of "ip:port"}`)

2. **Peer Discovery**:
   - Leechers query the tracker to discover available seeders
   - The tracker responds with a list of IP:port combinations of active peers

3. **Direct Socket Connections**:
   - Leechers establish direct TCP socket connections to each seeder:
     ```python
     s = socket.socket()
     s.connect((ip, port_num))
     ```
   - This direct connection works across the network as long as the ports are accessible

4. **Self-filtering**:
   - Peers filter themselves out from the peer list to avoid connecting to themselves:
     ```python
     my_ip = socket.gethostbyname(socket.gethostname())
     # Filter out connections to self
     ```

5. **Proper Cleanup**:
   - When a peer exits, it unregisters from the tracker:
     ```python
     unregister_msg = f"UNREGISTER|{os.path.basename(self.fname)}|{self.port}"
     ```
   - This keeps the peer list clean and up-to-date

6. **Multi-threaded Downloads**:
   - The system can download from multiple peers simultaneously using worker threads
   - Each thread handles the connection to a different peer
   - This enables faster downloads by getting different pieces from different computers

### Requirements for Multi-PC Operation

For the system to work across multiple computers:

1. The tracker's IP address must be accessible to all peers on the network
2. Firewall rules must allow connections to the tracker port (5000) and seeder ports
3. Each peer must have a unique combination of IP and port

## Requirements

- Python 3.6+
- `dropbox` Python package
- Internet connection for tracker communication and Dropbox access

## Installation

1. Clone this repository:
   ```
   git clone <repository-url>
   cd bittorrent-system
   ```

2. Install required dependencies:
   ```
   pip install dropbox
   ```

3. Configure your Dropbox token in the script or use the provided one.

### Obtaining a Dropbox Access Token

If you need to generate your own Dropbox access token:

1. Go to the [Dropbox Developer Console](https://www.dropbox.com/developers/apps)
2. Click "Create app"
3. Choose "Scoped access" API
4. Choose "Full Dropbox" access type
5. Give your app a name and click "Create app"
6. Go to the "Permissions" tab and enable:
   - `files.content.read`
   - `files.content.write`
7. Go back to the "Settings" tab and click "Generate access token"
8. Copy the generated token and replace the `DROPBOX_TOKEN` value in `bittorrent_system.py`

## Usage

The system offers four main commands:

### 1. Create a Torrent

```
python bittorrent_system.py create -f <file_path>
```

This command:
- Splits the file into 512KB pieces
- Calculates SHA1 hash for each piece
- Creates a .torrent metadata file
- Uploads the .torrent file to Dropbox

### 2. Run the Tracker

```
python bittorrent_system.py tracker
```

This starts the tracker server that coordinates peers. The tracker provides a command-line interface with these commands:
- `list`: Show all active torrents and number of peers
- `details`: Show detailed information about all torrents and their peers
- `info <torrent>`: Display metadata about a specific torrent
- `peers <torrent>`: List all peers for a specific torrent
- `exit`: Shut down the tracker

### Configuring Firewall for the Tracker

For the tracker to work properly, you need to allow incoming connections on port 5000 (default):

#### Windows:
1. Open Windows Defender Firewall with Advanced Security (search for it in the Start menu)
2. Click on "Inbound Rules" on the left panel
3. Click "New Rule..." on the right panel
4. Select "Port" and click "Next"
5. Select "TCP" and enter "5000" in the "Specific local ports" field
6. Click "Next" and select "Allow the connection"
7. Click "Next" twice (keeping default settings)
8. Name the rule "BitTorrent Tracker" and click "Finish"

#### Linux:
```bash
sudo ufw allow 5000/tcp
```

#### macOS:
```bash
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/bin/python3
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblock /usr/bin/python3
```

### 3. Seed a File

```
python bittorrent_system.py seed -f <torrent_name> -p <port> -t <tracker_ip>
```

This starts a seeder that:
- Registers with the tracker
- Listens for incoming connections on the specified port
- Serves file pieces to leechers
- Automatically unregisters when you press Enter to exit

**Note**: You may also need to configure your firewall to allow incoming connections on the port you specify for seeding.

### 4. Download (Leech) a File

```
python bittorrent_system.py leech -f <torrent_name> -p <port> -t <tracker_ip>
```

This starts a leecher that:
- Gets peer list from the tracker
- Downloads file pieces from multiple seeds concurrently
- Verifies the integrity of each piece using SHA1
- Automatically transitions to seeding mode after download completes

## Example Multi-PC Setup

1. On Computer A (IP: 192.168.1.100):
   ```
   python bittorrent_system.py tracker
   ```

2. On Computer B (IP: 192.168.1.101) - with the original file:
   ```
   python bittorrent_system.py create -f movie.mp4
   python bittorrent_system.py seed -f movie.mp4.torrent -p 6000 -t 192.168.1.100
   ```

3. On Computer C (IP: 192.168.1.102) - want to download:
   ```
   python bittorrent_system.py leech -f movie.mp4.torrent -p 6001 -t 192.168.1.100
   ```

4. On Computer D (IP: 192.168.1.103) - also want to download:
   ```
   python bittorrent_system.py leech -f movie.mp4.torrent -p 6002 -t 192.168.1.100
   ```

After Computers C and D complete their downloads, they automatically become seeders, allowing subsequent computers to download from multiple sources simultaneously.

## Architecture

### Torrent Metadata (.torrent files)

The .torrent files contain:
- Filename
- Total file size
- Piece size (512KB)
- SHA1 hash of each piece

### BitTorrent Protocol Messages

The system uses these message types:
- `CHOKE` (0)
- `UNCHOKE` (1)
- `INTERESTED` (2)
- `NOT_INTERESTED` (3)
- `HAVE` (4)
- `BITFIELD` (5)
- `REQUEST` (6)
- `PIECE` (7)
- `KEEPALIVE` (-1)

### Network Flow

1. Tracker runs on a known IP and port (default: 5000)
2. Seeders register with the tracker
3. Leechers query the tracker for peer lists
4. Leechers connect to seeders and download pieces
5. After download, leechers can become seeders

## Example Workflow

1. Generate a torrent from a file:
   ```
   python bittorrent_system.py create -f movie.mp4
   ```

2. Start the tracker in one terminal:
   ```
   python bittorrent_system.py tracker
   ```

3. Start a seeder in another terminal:
   ```
   python bittorrent_system.py seed -f movie.mp4.torrent -p 6000 -t localhost
   ```

4. Start a leecher in a third terminal:
   ```
   python bittorrent_system.py leech -f movie.mp4.torrent -p 6001 -t localhost
   ```

5. Monitor progress in the tracker terminal with the `details` command.

## Troubleshooting

- **Connection Refused Error**: Make sure the tracker is running before starting seeders or leechers. Also check that the firewall is properly configured to allow connections on the tracker port (5000).
- **No Peers Found**: Ensure at least one seeder is registered with the tracker.
- **Dropbox Token Error**: Update the DROPBOX_TOKEN if encountering authentication issues. Generate a new token following the instructions in the "Obtaining a Dropbox Access Token" section.
- **Torrent Not Found**: Check if the .torrent file exists in the Dropbox path (/SharedTorrents).
- **Port Already in Use**: Choose a different port if the specified port is unavailable.
- **Firewall Issues**: If you can't connect to the tracker or seeders, check your firewall settings to ensure the relevant ports are open.
- **Cross-Network Connection Issues**: If computers are on different subnets, make sure routing between subnets is allowed for the relevant ports.

## Limitations

- No NAT traversal or DHT support
- Centralized tracker (single point of failure)
- No encryption or privacy features
- No partial download resumption

## Future Improvements

- Implement DHT for trackerless operation
- Add UPnP for automatic port forwarding
- Support partial downloads and resumption
- Add encryption and privacy features
- Implement rate limiting and bandwidth management
