import os
import socket
import threading
import hashlib
import json
import argparse
import time
import queue
import struct
import dropbox
from dropbox.exceptions import AuthError, ApiError

# === Configuration ===
PIECE_SIZE     = 512 * 1024         # 512 KB per piece
DROPBOX_PATH   = '/SharedTorrents'  # Dropbox folder for .torrent JSON
DROPBOX_TOKEN  = "sl.u.AFqym1pMw-9PjZ5DH8LHBY2Nr2CS3uUkxc-z2I0_I0xPnGsAif5fTLMhkrEp0L1teDSkAA0Dx_OcyEr8dF8thBuNUSBDHAik4GiC8XXBcv49PfvW3Hhagu1hqrvjWJZIheC8ZxbT8A1k94pT8t4AI0hwxUB4cGLiwdaoUc0F0SZRDMYGQSNRxKEdhMqj2V8D8DtiuPx8cHtM0aSQmrIDC3mjCA5hUbNiKR-qaDkYMLznGQoyOJw4AiBx35RGBYGCvN6nf2rxH0MvJuWhyQaG1gxLBIZX7Q1JAE3Rbc4U0NG3M_tUFV-FIt0Qni5fs0ObrM5Ch2BtNmRT1aApBuvrngJJ6O_oFQK8rMc2dya5jCnLcg9z5IO0YgP584Zm7Prl1Lsg-j0xwfCSXLgUSdBg0W9pZA5s14buP_4sTT0cBC4AHijKfjvn4IX0ObEtV5uGrb7IZ5sTnPvxc9kTlX9b8uNVYhQmIa0rwVQ4BN52VwPU_9V1eBJKH236VQbMBLPQyI7nrlsxxIvtEymm7g7ZahXfGsoWiCcjYqJ_UIlBbo-PK97loYJn_Sqswd6yCLDYqiRofb_iyxn-PyteulscEfI_Uo3vRrkbSvlagkl7ua5_L3iWWWdnH-NCOTl21tHy2OvHE7UX70TDH_w37Wv06DEvDcHBN6kGggPXD3kVYoOiAB5Kcp_UckxwjoIubTxvMn280f5dM_lsE1CVQJVrgULgscUrp47sFSdzS0efikTW76NVYAlY3a5nGVam24_rIfVoWlfyjneJm4qg_TOqa7ujAQ4G9LgJYE54UqAAwh7-BvPHFy43TnNkTY_Yt1bxozwGnugq-OTCbVO7K7pQKoyXtBYSueqK2zhA5oS6uz638lTyO8Ee1jP7QLK84vpfWSQSzQNkFSJ0G81OEOehY0nUR61g_aCkEHVzkviGAiCS0Lj5bC5kyF1DWIQYaXQav-r2YOiKR1T7dzoIO8TwPTnjsbwJ9nX2a5cxiGpyCbwxfO-PoPf0DNeCIFzHf3VdFqPgtngbj-1P4ONBi8TlO5P4LnFCaewSjPtWlajJSb8-bwM0R3wgH8tOPdLaqQeD0bXHVLAg9n1GtNR2aQeO-QQtNewjgux5A0ANJ9kaOEbi0LZLyLmNsXWyxL-ho7yOA6vS-6P9gtq0wMRLfiGYEpSutAJQNhkElRLNKwT9x4yMTWMYcmfTHVoTxd3fAF-_DPdfi0vvIHkXT753-Fd5conh1CxUxsirGdzu99-aA-Ma_9xiujwfQgpM9gQspyicNK6tBr9qJEK82UTdDRJ2oRqgCCwf6BZxKWLk1nnRJkflQA2K-3ZXsVxwVKD-hVUvP4vaahZLJeo6Pr9IrlnCAiuHTNdQXFIsESFh-4d5COxNXVBsMvoHD4aGozfMTeu9yRLV4fMX6ieTqUGeBnYb5fHk"
TRACKER_PORT   = 5000               # default tracker port
MAX_WORKERS    = 5                  # max concurrent piece fetchers

# === BitTorrent Message IDs ===
MSG_CHOKE          = 0
MSG_UNCHOKE        = 1
MSG_INTERESTED     = 2
MSG_NOT_INTERESTED = 3
MSG_HAVE           = 4
MSG_BITFIELD       = 5
MSG_REQUEST        = 6
MSG_PIECE          = 7
MSG_KEEPALIVE      = -1  # keep-alive message

# send a length-prefixed message

def send_message(sock, msg_id, payload=b''):
    if msg_id == MSG_KEEPALIVE:
        sock.sendall(struct.pack('!I', 0))
        return
    length = 1 + len(payload)
    sock.sendall(struct.pack('!I', length) + struct.pack('!B', msg_id) + payload)

# recv a single message

def recv_message(sock):
    hdr = sock.recv(4)
    if not hdr:
        return None, None
    length = struct.unpack('!I', hdr)[0]
    if length == 0:
        return MSG_KEEPALIVE, b''
    
    # Ensure the full message ID is received
    msg_id_bytes = b''
    while len(msg_id_bytes) < 1:
        chunk = sock.recv(1 - len(msg_id_bytes))
        if not chunk:
            raise ConnectionError("Socket closed while receiving message ID")
        msg_id_bytes += chunk
    msg_id = struct.unpack('!B', msg_id_bytes)[0]

    # Ensure the full payload is received
    payload_len = length - 1
    payload = b''
    while len(payload) < payload_len:
        chunk = sock.recv(payload_len - len(payload))
        if not chunk:
            raise ConnectionError("Socket closed while receiving payload")
        payload += chunk

    return msg_id, payload

# === Torrent Metadata ===

def create_torrent(file_path):
    size = os.path.getsize(file_path)
    pieces = []
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(PIECE_SIZE)
            if not chunk:
                break
            pieces.append(hashlib.sha1(chunk).hexdigest())
    meta = {
        'filename': os.path.basename(file_path),
        'size': size,
        'piece_size': PIECE_SIZE,
        'pieces': pieces
    }
    if not DROPBOX_TOKEN:
        raise RuntimeError("Set DROPBOX_ACCESS_TOKEN env var")
    dbx = dropbox.Dropbox(DROPBOX_TOKEN)
    data = json.dumps(meta, indent=2).encode()
    remote = f"{DROPBOX_PATH}/{os.path.basename(file_path)}.torrent"
    dbx.files_upload(data, remote, mode=dropbox.files.WriteMode.overwrite)
    print(f"[+] Uploaded torrent metadata to Dropbox: {remote}")

# === Tracker Implementation ===
peers = {}  # torrent_name -> set of "ip:port"

class TrackerServer(threading.Thread):
    def __init__(self, port=TRACKER_PORT):
        super().__init__(daemon=True)
        self.port = port

    def run(self):
        server = socket.socket()
        server.bind(('0.0.0.0', self.port))
        server.listen(5)
        print(f"[TRACKER] Running on port {self.port}")
        while True:
            conn, addr = server.accept()
            threading.Thread(target=self.handle, args=(conn, addr), daemon=True).start()

    def handle(self, conn, addr):
        try:
            data = conn.recv(1024).decode()
            cmd, torrent, port = data.split('|')
            peer_str = f"{addr[0]}:{port}"
            
            if cmd == 'REGISTER':
                peers.setdefault(torrent, set()).add(peer_str)
                conn.send(b'OK')
                print(f"[TRACKER] Registered peer {peer_str} for '{torrent}'")
            elif cmd == 'UNREGISTER':
                if torrent in peers and peer_str in peers[torrent]:
                    peers[torrent].discard(peer_str)
                    print(f"[TRACKER] Unregistered peer {peer_str} from '{torrent}'")
                    # Clean up empty torrent entries
                    if not peers[torrent]:
                        del peers[torrent]
                        print(f"[TRACKER] Removed empty torrent entry '{torrent}'")
                conn.send(b'OK')
            elif cmd == 'GET':
                plist = peers.get(torrent, set())
                conn.send('|'.join(plist).encode())
        except Exception as e:
            print(f"[TRACKER ERROR] {e}")
        finally:
            conn.close()

# Tracker CLI

def tracker_cli():
    print("Tracker CLI. commands: list, info <torrent>, peers <torrent>, details, exit")
    while True:
        cmd = input('tracker> ').strip().split()
        if not cmd:
            continue
        action = cmd[0]
        if action == 'list':
            for t, pset in peers.items():
                print(f"{t}: {len(pset)} peer(s)")
        elif action == 'details':
            # Enhanced list command to show all torrents with detailed peer information
            if not peers:
                print("No active torrents found.")
            else:
                for t, pset in peers.items():
                    print(f"\n[Torrent] {t}: {len(pset)} peer(s)")
                    if pset:
                        print("  Peers:")
                        for i, peer in enumerate(pset, 1):
                            ip, port = peer.split(':')
                            print(f"    {i}. {ip}:{port}")
                    else:
                        print("  No peers connected")
        elif action == 'info' and len(cmd) == 2:
            meta = fetch_meta(cmd[1])
            print(json.dumps(meta, indent=2))
        elif action == 'peers' and len(cmd) == 2:
            for p in peers.get(cmd[1], []):
                print(p)
        elif action == 'exit':
            print("Shutting down tracker CLI.")
            break
        else:
            print("Unknown command")

# Fetch torrent metadata from Dropbox

def fetch_meta(torrent_name):
    if not DROPBOX_TOKEN:
        raise RuntimeError("Set DROPBOX_ACCESS_TOKEN env var")
    dbx = dropbox.Dropbox(DROPBOX_TOKEN)
    path = f"{DROPBOX_PATH}/{torrent_name}"
    _, res = dbx.files_download(path)
    return json.loads(res.content.decode())

# === Peer (Seeder & Leecher) ===
class Peer:
    def __init__(self, torrent, tracker_ip, port, mode):
        self.meta = fetch_meta(torrent)
        self.tracker_ip = tracker_ip
        self.port = port
        self.mode = mode  # 'seed' or 'leech'
        self.fname = self.meta['filename']
        self.size = self.meta['size']
        self.pieces = self.meta['pieces']
        self.total = len(self.pieces)
        self.done = [False] * self.total
        self.lock = threading.Lock()
        
        # Display torrent metadata for debugging
        print(f"[INFO] Torrent: {torrent}")
        print(f"[INFO] Filename: {self.fname}")
        print(f"[INFO] Total size: {self.size} bytes")
        print(f"[INFO] Piece size: {self.meta.get('piece_size', PIECE_SIZE)} bytes")
        print(f"[INFO] Total pieces: {self.total}")
        print(f"[INFO] First piece hash: {self.pieces[0][:16]}...")
        if self.total > 1:
            print(f"[INFO] Last piece hash: {self.pieces[-1][:16]}...")
        
        if mode == 'seed':
            # Verify the local file
            try:
                actual_size = os.path.getsize(self.fname)
                print(f"[INFO] Local file size: {actual_size} bytes")
                if actual_size != self.size:
                    print(f"[WARNING] File size mismatch! Torrent: {self.size}, Local: {actual_size}")
            except FileNotFoundError:
                print(f"[ERROR] File not found: {self.fname}")
        elif mode == 'leech':
            self.outf = open(self.fname, 'wb')
            self.outf.truncate(self.size)

    def register(self):
        # Get peer list from the tracker
        try:
            if self.mode == 'seed':
                # Register with the tracker
                print(f"[DEBUG] Registering with tracker at {self.tracker_ip}:{TRACKER_PORT} as seed")
                try:
                    sock_reg = socket.socket()
                    sock_reg.settimeout(10)  # Add timeout
                    sock_reg.connect((self.tracker_ip, TRACKER_PORT))
                    register_msg = f"REGISTER|{os.path.basename(self.fname)}|{self.port}".encode()
                    print(f"[DEBUG] Sending register message: {register_msg}")
                    sock_reg.send(register_msg)
                    response = sock_reg.recv(1024)
                    print(f"[DEBUG] Received response from tracker: {response}")
                finally:
                    sock_reg.close()
            
            # Get peer list from the tracker (both seed and leech do this)
            print(f"[DEBUG] Getting peer list from tracker at {self.tracker_ip}:{TRACKER_PORT}")
            sock_get = socket.socket()
            sock_get.settimeout(10)  # Add timeout
            sock_get.connect((self.tracker_ip, TRACKER_PORT))
            get_msg = f"GET|{os.path.basename(self.fname)}|0".encode()
            print(f"[DEBUG] Sending GET message: {get_msg}")
            sock_get.send(get_msg)
            raw = sock_get.recv(4096).decode().split('|')
            sock_get.close()
            
            # Filter out empty peer strings
            self.peers = [p for p in raw if p]
            print(f"[DEBUG] Received peers from tracker: {self.peers}")
            print(f"[+] Tracker returned {len(self.peers)} peers")
            
            # Verify we have peers if we're a leecher
            if self.mode == 'leech' and not self.peers:
                print("[WARNING] Tracker returned no peers. Make sure there is at least one seeder running.")
        except Exception as e:
            print(f"[ERROR] Failed to communicate with tracker: {type(e).__name__}: {str(e)}")
            self.peers = []

    def unregister(self):
        try:
            print(f"[DEBUG] Unregistering from tracker at {self.tracker_ip}:{TRACKER_PORT}")
            sock = socket.socket()
            sock.settimeout(10)
            sock.connect((self.tracker_ip, TRACKER_PORT))
            unregister_msg = f"UNREGISTER|{os.path.basename(self.fname)}|{self.port}".encode()
            print(f"[DEBUG] Sending unregister message: {unregister_msg}")
            sock.send(unregister_msg)
            response = sock.recv(1024)
            print(f"[DEBUG] Received response from tracker: {response}")
            sock.close()
            print(f"[+] Unregistered {self.fname} from tracker")
        except Exception as e:
            print(f"[ERROR] Failed to unregister: {type(e).__name__}: {str(e)}")

    def start(self):
        self.register()
        try:
            if self.mode == 'seed':
                threading.Thread(target=self.serve, daemon=True).start()
                input('Press ENTER to stop seeding...')
            else:
                self.download()
        finally:
            # Always unregister when exiting, whether seed or leech
            self.unregister()

    def serve(self):
        # Verify the seeded file exists before starting the server
        try:
            if not os.path.exists(self.fname):
                print(f"[ERROR] File '{self.fname}' not found! Cannot serve non-existent file.")
                return
            actual_size = os.path.getsize(self.fname)
            if actual_size != self.size:
                print(f"[WARNING] File size mismatch! Torrent: {self.size} bytes, Actual: {actual_size} bytes.")
        except Exception as e:
            print(f"[ERROR] File check failed: {str(e)}")
            return
        
        try:
            server = socket.socket()
            server.bind(('0.0.0.0', self.port))
            server.listen(5)
            print(f"[SEED] Serving '{self.fname}' on port {self.port}")
            
            while True:
                try:
                    conn, addr = server.accept()
                    print(f"[SEED] New connection from {addr[0]}:{addr[1]}")
                    threading.Thread(target=self.handle_peer, args=(conn, addr), daemon=True).start()
                except Exception as e:
                    print(f"[ERROR] Error accepting connection: {str(e)}")
        except Exception as e:
            print(f"[ERROR] Failed to start seeder server: {str(e)}")
        finally:
            if 'server' in locals():
                server.close()
            
    def handle_peer(self, conn, addr=None):
        try:
            # Log which peer we're handling
            peer_addr = f"{addr[0]}:{addr[1]}" if addr else "unknown"
            print(f"[SEED] Handling peer {peer_addr}")
            
            # Receive handshake
            try:
                handshake = conn.recv(68)  # handshake
                print(f"[SEED] Received handshake from {peer_addr}: {handshake[:10]}...")
            except Exception as e:
                print(f"[ERROR] Failed to receive handshake from {peer_addr}: {str(e)}")
                return
                
            # Send handshake response
            conn.sendall(b'BTMSG' + struct.pack('!I', self.total))
            print(f"[SEED] Sent handshake response to {peer_addr}")
            
            # bitfield
            bf = bytearray((self.total + 7) // 8)
            for i in range(self.total): bf[i // 8] |= 1 << (7 - i % 8)
            print(f"[SEEDER DEBUG] Sending bitfield for {self.total} pieces: {bytes(bf).hex()}")
            send_message(conn, MSG_BITFIELD, bytes(bf))
            print(f"[SEED] Sent bitfield to {peer_addr}")
            
            # serve requests
            while True:
                try:
                    msg_id, payload = recv_message(conn)
                    print(f"[SEED] Received message type {msg_id} from {peer_addr}")
                    
                    if msg_id == MSG_REQUEST:
                        idx = struct.unpack('!I', payload)[0]
                        print(f"[SEEDER DEBUG] Received request for piece {idx} from {peer_addr}")
                        try:
                            with open(self.fname, 'rb') as f:
                                f.seek(idx * PIECE_SIZE)
                                data = f.read(PIECE_SIZE)
                                if not data:
                                    print(f"[SEEDER ERROR] Failed to read piece {idx}: returned empty data")
                                    continue
                                print(f"[SEEDER DEBUG] Read piece {idx} from file, size={len(data)}")
                                data_hash = hashlib.sha1(data).hexdigest()
                                print(f"[SEEDER DEBUG] Piece {idx} hash: {data_hash[:16]}...")
                                if data_hash != self.pieces[idx]:
                                    print(f"[SEEDER WARNING] Piece {idx} hash mismatch! Calculated: {data_hash[:16]}..., Expected: {self.pieces[idx][:16]}...")
                            
                            send_message(conn, MSG_PIECE, struct.pack('!I', idx) + data)
                            print(f"[SEEDER DEBUG] Sent piece {idx} to {peer_addr}, size={len(data)}")
                        except Exception as e:
                            print(f"[SEEDER ERROR] Failed to serve piece {idx} to {peer_addr}: {type(e).__name__}: {str(e)}")
                    elif msg_id is None:
                        print(f"[SEED] Peer {peer_addr} disconnected")
                        break
                except Exception as e:
                    print(f"[SEED] Error serving {peer_addr}: {str(e)}")
                    break
        except Exception as e:
            print(f"[ERROR] Error in handle_peer: {str(e)}")
        finally:
            conn.close()
            print(f"[SEED] Connection closed with {peer_addr if 'peer_addr' in locals() else 'unknown peer'}")

    def download(self):
        q = queue.Queue()
        for i in range(self.total):
            q.put(i)
        
        # Start timing the download
        download_start_time = time.time()
        
        print(f"[DEBUG] Starting download of {self.fname} with {self.total} pieces")

        def worker(peer_addr):
            print(f"[DEBUG] Worker started for peer {peer_addr}")
            ip, prt = peer_addr.split(':')
            port_num = int(prt)
            
            # Skip connecting to ourselves
            if ip == socket.gethostbyname(socket.gethostname()) and port_num == self.port:
                print(f"[DEBUG] Skipping connection to self ({peer_addr})")
                return
                
            while not q.empty():
                try:
                    idx = q.get_nowait()
                    print(f"[DEBUG] Worker {peer_addr} attempting to fetch piece {idx}")
                except queue.Empty:
                    print(f"[DEBUG] Queue empty, worker {peer_addr} exiting")
                    return
                
                try:
                    print(f"[DEBUG] Connecting to {ip}:{port_num}")
                    s = socket.socket()
                    s.settimeout(10)  # Add a timeout to avoid hanging
                    s.connect((ip, port_num))
                    print(f"[DEBUG] Connection established with {peer_addr}")
                    
                    # handshake
                    print(f"[DEBUG] Sending handshake to {peer_addr}")
                    s.sendall(b'BTMSG' + struct.pack('!I', self.total))
                    handshake_response = s.recv(68)
                    print(f"[DEBUG] Received handshake response: {handshake_response[:10]}...")
                    
                    # interested & bitfield
                    print(f"[DEBUG] Sending interested message to {peer_addr}")
                    send_message(s, MSG_INTERESTED)
                    print(f"[DEBUG] Waiting for bitfield from {peer_addr}")
                    msg, payload = recv_message(s)
                    print(f"[DEBUG] Received message type {msg} from {peer_addr}")
                    
                    if msg != MSG_BITFIELD:
                        print(f"[DEBUG] Error: Expected bitfield (5), got {msg} from {peer_addr}")
                        raise RuntimeError('No bitfield')
                    
                    bf = payload
                    print(f"[LEECHER DEBUG {peer_addr}] Received bitfield: {bf.hex()}")
                    
                    # DEBUG: Check specifically for piece 0
                    has_piece_0 = False
                    if len(bf) > 0:
                        has_piece_0 = bool(bf[0] & (1 << 7))  # First bit of first byte
                    print(f"[LEECHER DEBUG {peer_addr}] Peer has piece 0? {has_piece_0}")
                    
                    # Check if all pieces are available - show which ones peer has
                    for i in range(min(self.total, 16)):  # Show up to 16 pieces
                        byte_idx = i // 8
                        bit_idx = 7 - (i % 8)
                        has_piece = False
                        if byte_idx < len(bf):
                            has_piece = bool(bf[byte_idx] & (1 << bit_idx))
                        print(f"[DEBUG] Peer {peer_addr} has piece {i}? {has_piece}")
                    
                    # Safer bitfield check with proper index validation
                    byte_idx = idx // 8
                    bit_idx = 7 - (idx % 8)
                    if byte_idx >= len(bf) or not (bf[byte_idx] & (1 << bit_idx)):
                        print(f"[LEECHER DEBUG {peer_addr}] Peer lacks piece {idx}, putting back in queue.")
                        raise RuntimeError(f'Peer lacks piece {idx}')
                    
                    # request piece
                    print(f"[DEBUG] Requesting piece {idx} from {peer_addr}")
                    send_message(s, MSG_REQUEST, struct.pack('!I', idx))
                    print(f"[DEBUG] Waiting for piece data from {peer_addr}")
                    msg2, payload2 = recv_message(s)
                    print(f"[DEBUG] Received response message type {msg2} from {peer_addr}")
                    
                    if msg2 == MSG_PIECE:
                        ridx = struct.unpack('!I', payload2[:4])[0]
                        print(f"[DEBUG] Received piece {ridx} (requested {idx}) from {peer_addr}, length {len(payload2)-4}")
                        data = payload2[4:]
                        data_hash = hashlib.sha1(data).hexdigest()
                        expected_hash = self.pieces[ridx]
                        print(f"[DEBUG] Piece {ridx} hash check: calculated={data_hash[:8]}..., expected={expected_hash[:8]}...")
                        
                        if data_hash == expected_hash:
                            with self.lock:
                                if not self.done[ridx]:
                                    print(f"[DEBUG] Writing piece {ridx} to file")
                                    self.outf.seek(ridx * PIECE_SIZE)
                                    self.outf.write(data)
                                    self.outf.flush()  # Ensure data is written to disk
                                    self.done[ridx] = True
                                    print(f"[+] Piece {ridx+1}/{self.total} fetched from {peer_addr}")
                                else:
                                    print(f"[DEBUG] Piece {ridx} already downloaded, skipping")
                        else:
                            print(f"[!] Corrupt piece {ridx+1} from {peer_addr}. Hash mismatch.")
                            print(f"[DEBUG] Hash details: calculated={data_hash}, expected={expected_hash}")
                            q.put(ridx)
                    else:
                        print(f"[DEBUG] Expected piece (7), got message type {msg2} from {peer_addr}")
                        q.put(idx)
                    
                    s.close()
                    print(f"[DEBUG] Closed connection to {peer_addr}")
                    
                except Exception as e:
                    print(f"[DEBUG] Error with {peer_addr}: {type(e).__name__}: {str(e)}")
                    q.put(idx)
                finally:
                    time.sleep(0.1)

        # Filter out and skip connection to ourselves
        my_ip = socket.gethostbyname(socket.gethostname())
        print(f"[DEBUG] My IP address: {my_ip}, My port: {self.port}")

        valid_peers = []
        for peer_addr in self.peers:
            peer_ip, peer_port = peer_addr.split(':')
            peer_port_num = int(peer_port)
            if peer_ip == my_ip and peer_port_num == self.port:
                print(f"[DEBUG] Skipping self in peer list: {peer_addr}")
            else:
                valid_peers.append(peer_addr)
        
        if not valid_peers:
            print("[ERROR] No valid peers found (excluding self). Download cannot proceed.")
            if hasattr(self, 'outf'):
                self.outf.close()
            return
            
        print(f"[DEBUG] Starting {min(len(valid_peers), MAX_WORKERS)} worker threads from {len(valid_peers)} valid peers")
        
        threads = []
        for peer_addr in valid_peers[:MAX_WORKERS]:
            print(f"[DEBUG] Creating worker thread for peer {peer_addr}")
            t = threading.Thread(target=worker, args=(peer_addr,), daemon=True)
            t.start()
            threads.append(t)
        
        if not threads:
            print("[ERROR] No worker threads started. Download cannot proceed.")
            if hasattr(self, 'outf'):
                self.outf.close()
            return
            
        print(f"[DEBUG] Waiting for {len(threads)} worker threads to complete")
        for t in threads:
            t.join()

        # Calculate download time
        download_end_time = time.time()
        download_time = download_end_time - download_start_time
        
        # Make sure all downloaded pieces are written to disk
        if hasattr(self, 'outf'):
            self.outf.flush()
            self.outf.close()
            print("[DEBUG] Output file closed")

        incomplete = [i for i, done in enumerate(self.done) if not done]
        if incomplete:
            print(f"[WARNING] Download incomplete. Missing pieces: {incomplete}")
            print(f"[WARNING] Downloaded {self.total - len(incomplete)}/{self.total} pieces.")
            print(f"[INFO] Time elapsed: {download_time:.2f} seconds")
        else:
            # Format time nicely: show in minutes and seconds if > 60 seconds
            if download_time >= 60:
                minutes = int(download_time // 60)
                seconds = download_time % 60
                time_str = f"{minutes} min {seconds:.2f} sec"
            else:
                time_str = f"{download_time:.2f} seconds"
                
            file_size_mb = self.size / (1024 * 1024)
            download_speed = file_size_mb / download_time
            
            print(f"[DONE] Download complete: '{self.fname}'")
            print(f"[INFO] Download time: {time_str}")
            print(f"[INFO] File size: {file_size_mb:.2f} MB")
            print(f"[INFO] Average download speed: {download_speed:.2f} MB/s")
            
            print("[INFO] Transitioning from leech mode to seed mode...")
            # Change mode from 'leech' to 'seed'
            self.mode = 'seed'
            # Register as seeder with the tracker
            try:
                sock_reg = socket.socket()
                sock_reg.settimeout(10)
                sock_reg.connect((self.tracker_ip, TRACKER_PORT))
                register_msg = f"REGISTER|{os.path.basename(self.fname)}|{self.port}".encode()
                print(f"[DEBUG] Sending register message as new seeder: {register_msg}")
                sock_reg.send(register_msg)
                response = sock_reg.recv(1024)
                print(f"[DEBUG] Received response from tracker: {response}")
                sock_reg.close()
                print("[INFO] Successfully registered as seeder with tracker")
                # Start seeding
                print("[INFO] Starting to seed the downloaded file")
                threading.Thread(target=self.serve, daemon=True).start()
                print("[INFO] Seeding in background. Press ENTER to stop seeding...")
                # Keep running as a seeder until user stops
                input()
            except Exception as e:
                print(f"[ERROR] Failed to transition to seed mode: {type(e).__name__}: {str(e)}")

# === Main CLI ===
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)

    c1 = sub.add_parser('create')
    c1.add_argument('-f', '--file', required=True,
                    help="Path to file to create torrent for")

    c2 = sub.add_parser('tracker')

    c3 = sub.add_parser('seed')
    c3.add_argument('-f', '--torrent', required=True,
                    help="Name of .torrent (in Dropbox)")
    c3.add_argument('-p', '--port', type=int, required=True,
                    help="Port to serve pieces on")
    c3.add_argument('-t', '--tracker', required=True,
                    help="IP address of tracker (default port 5000)")

    c4 = sub.add_parser('leech')
    c4.add_argument('-f', '--torrent', required=True,
                    help="Name of .torrent (in Dropbox)")
    c4.add_argument('-p', '--port', type=int, required=True,
                    help="Local port (required, not used)")
    c4.add_argument('-t', '--tracker', required=True,
                    help="IP address of tracker (default port 5000)")

    args = parser.parse_args()
    if args.cmd == 'create':
        create_torrent(args.file)
    elif args.cmd == 'tracker':
        TrackerServer().start()
        tracker_cli()
    else:
        torrent_name = args.torrent
        mode = 'seed' if args.cmd == 'seed' else 'leech'
        peer = Peer(torrent_name, args.tracker, args.port, mode)
        peer.start()