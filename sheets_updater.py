import os
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import socket
import struct
import re

CREDENTIALS_FILE = "myself-420720-9bf2e71023ba.json"
SPREADSHEET_ID = "11h4v3WjWEm5qN97350vyt8XWbbw2Sh-pyfA_7nQj8X4"

SERVERS_FOLDER = 'serwery'
INTERVAL = 60  # Jeden główny interwał na całą pętlę

scope = [
    "https://spreadsheets.google.com/feeds", 
    "https://www.googleapis.com/auth/spreadsheets", 
    "https://www.googleapis.com/auth/drive.file", 
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SPREADSHEET_ID)

sheet_main = spreadsheet.sheet1
if sheet_main.cell(1,1).value != 'Timestamp':
    sheet_main.update('A1', [['Timestamp', 'ServerName', 'IP:Port', 'PlayersCount', 'GameMode']])

def debug_log(message: str):
    print(f"[DEBUG] {message}")

def parse_server_file(filepath):
    server_name = 'N/A'
    ip_port = 'N/A'
    game_mode = 'N/A'
    players_count = None

    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines:
        line_strip = line.strip()
        if line_strip.startswith("Serwer:"):
            server_name = line_strip.replace("Serwer:", "").strip()
        elif line_strip.startswith("Adres:"):
            ip_port = line_strip.replace("Adres:", "").strip()
        elif line_strip.startswith("Tryb gry:"):
            game_mode = line_strip.replace("Tryb gry:", "").strip()
        if line_strip.startswith("====================================="):
            break

    last_players_line = None
    for line in reversed(lines):
        if "Liczba graczy" in line:
            last_players_line = line.strip()
            break

    if last_players_line:
        numbers = re.findall(r'\d+', last_players_line)
        if numbers:
            players_count = int(numbers[-1])
    
    return server_name, ip_port, game_mode, players_count

def a2s_request(address, request_data, timeout=3.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(request_data, address)
        data, _ = sock.recvfrom(4096)
        return data
    except socket.timeout:
        return None
    except socket.error as e:
        debug_log(f"Błąd soketu przy A2S do {address}: {e}")
        return None
    finally:
        sock.close()

def get_a2s_player_list(ip, port):
    address = (ip, port)
    request = b"\xFF\xFF\xFF\xFFU\xFF\xFF\xFF\xFF"
    response = a2s_request(address, request)
    if not response or len(response) < 9 or response[4] != ord('A'):
        return []

    challenge = response[5:9]
    request = b"\xFF\xFF\xFF\xFFU" + challenge
    response = a2s_request(address, request)
    if not response or len(response) < 6 or response[4] != ord('D'):
        return []

    num_players = response[5]
    offset = 6
    players = []

    for _ in range(num_players):
        if offset + 1 > len(response):
            break
        index = response[offset]
        offset += 1
        end_name = response.find(b'\x00', offset)
        if end_name == -1:
            break
        name = response[offset:end_name].decode('utf-8', errors='ignore')
        offset = end_name + 1
        if offset + 8 > len(response):
            break
        score = struct.unpack('<l', response[offset:offset+4])[0]
        offset += 4
        duration = struct.unpack('<f', response[offset:offset+4])[0]
        offset += 4

        players.append({
            'name': name,
            'score': score,
            'duration': duration
        })

    return players

def get_server_worksheet(server_name):
    try:
        wks = spreadsheet.worksheet(server_name)
    except gspread.exceptions.WorksheetNotFound:
        wks = spreadsheet.add_worksheet(title=server_name, rows=1000, cols=5)
        wks.update('A1', [['Timestamp', 'PlayerName', 'Score', 'Duration']])
    return wks

while True:
    # Jedna iteracja pętli: przeglądamy wszystkie pliki, aktualizujemy dane,
    # a następnie czekamy INTERVAL sekund.
    files = [f for f in os.listdir(SERVERS_FOLDER) if f.endswith(".txt")]

    for filename in files:
        filepath = os.path.join(SERVERS_FOLDER, filename)
        server_name, ip_port, game_mode, players_count = parse_server_file(filepath)

        # Aktualizacja głównego arkusza
        if players_count is not None:
            row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), server_name, ip_port, players_count, game_mode]
            sheet_main.append_row(row, value_input_option='RAW')
            print(f"Wpisano do głównego arkusza: {row}")

        # Lista graczy
        if ':' in ip_port:
            ip, port_str = ip_port.split(':')
            port = int(port_str)
            players = get_a2s_player_list(ip, port)
            if players:
                server_wks = get_server_worksheet(server_name)
                for p in players:
                    player_row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), p['name'], p['score'], p['duration']]
                    server_wks.append_row(player_row, value_input_option='RAW')
                    print(f"Wpisano gracza do arkusza {server_name}: {player_row}")
            else:
                print(f"Brak danych o graczach dla serwera {server_name} (A2S brak odpowiedzi).")

    # Po przetworzeniu wszystkich plików czekamy INTERVAL sekund
    print(f"Czekam {INTERVAL} sekund przed kolejną iteracją...")
    time.sleep(INTERVAL)
