import os
import time
import requests
import socket
import struct
from datetime import datetime
from dotenv import load_dotenv

DEBUG = True

def debug_log(message: str):
    if DEBUG:
        print(f"[DEBUG] {message}")

# Ładowanie zmiennych z pliku .env
load_dotenv()
BATTLEMETRICS_TOKEN = os.getenv("BATTLEMETRICS_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")  # Nie będzie użyty, ale pozostawiamy w kodzie

if BATTLEMETRICS_TOKEN is None:
    raise ValueError("Brak tokenu BATTLEMETRICS_TOKEN w .env")

if STEAM_API_KEY is None:
    debug_log("Brak klucza STEAM_API_KEY w .env. Nie jest to wymagane do A2S, ale ostrzegamy.")

INTERVAL = 60
previous_player_counts = {}  # {server_id: player_count}

# Upewnij się, że istnieje folder "serwery"
if not os.path.exists("serwery"):
    os.makedirs("serwery")

def get_polish_gmod_servers():
    """
    Pobiera listę serwerów Garry's Mod zlokalizowanych w Polsce z BattleMetrics.
    Zwraca listę krotek: (server_id, name, ip, port, serverSteamId)
    """
    url = "https://api.battlemetrics.com/servers"
    params = {
        'filter[game]': 'gmod',
        'filter[countries]': 'PL',
        'page[size]': '100'
    }
    headers = {
        'Authorization': f'Bearer {BATTLEMETRICS_TOKEN}'
    }

    debug_log(f"Wysyłam żądanie do BattleMetrics API: {url}, params={params}")
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
    except requests.RequestException as e:
        debug_log(f"Błąd połączenia z BattleMetrics API: {e}")
        return []

    if r.status_code != 200:
        debug_log(f"Niepoprawny kod statusu od BattleMetrics API: {r.status_code}")
        return []

    data = r.json()
    servers = data.get('data', [])
    debug_log(f"Pobrano listę serwerów: {len(servers)} serwerów znaleziono.")

    result = []
    for srv in servers:
        srv_id = srv.get('id')
        attributes = srv.get('attributes', {})
        ip = attributes.get('ip', '')
        port = attributes.get('port')
        name = attributes.get('name', 'N/A')
        details = attributes.get('details', {})
        server_steam_id = details.get('serverSteamId')
        if ip and port and srv_id and server_steam_id:
            result.append((srv_id, name, ip, port, server_steam_id))
    return result

def get_server_details(server_id):
    """
    Pobiera szczegółowe informacje o serwerze z BattleMetrics API.
    Zwraca: (server_name, player_count, game_mode)
    """
    url = f"https://api.battlemetrics.com/servers/{server_id}"
    headers = {
        'Authorization': f'Bearer {BATTLEMETRICS_TOKEN}'
    }

    debug_log(f"Wysyłam żądanie o szczegóły serwera {server_id} do BattleMetrics API.")
    try:
        r = requests.get(url, headers=headers, timeout=10)
    except requests.RequestException as e:
        debug_log(f"Błąd połączenia z BattleMetrics API: {e}")
        return "N/A", 0, "N/A"

    if r.status_code != 200:
        debug_log(f"Niepoprawny kod statusu od BattleMetrics API: {r.status_code}")
        return "N/A", 0, "N/A"

    data = r.json().get('data', {})
    attributes = data.get('attributes', {})
    details = attributes.get('details', {})
    server_name = attributes.get('name', 'N/A')
    player_count = attributes.get('players', 0)
    game_mode = details.get('gameMode', 'N/A')
    return server_name, player_count, game_mode

def get_server_log_filename(server_id, server_name):
    safe_name = "".join(c for c in server_name if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
    if not safe_name:
        safe_name = "no_name"
    filename = f"serwer_{server_id}_{safe_name}.txt"
    return os.path.join("serwery", filename)

def ensure_server_file_exists(filename, server_name, ip, port, game_mode, server_steam_id):
    """
    Sprawdza czy plik istnieje, jeśli nie - tworzy go i zapisuje nagłówek.
    """
    if not os.path.exists(filename):
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"Serwer: {server_name}\n")
            f.write(f"Adres: {ip}:{port}\n")
            f.write(f"Tryb gry: {game_mode}\n")
            f.write(f"ServerSteamID: {server_steam_id}\n")
            f.write("=====================================\n")

def log_to_server_file(filename, message):
    with open(filename, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

def a2s_request(address, request_data, timeout=3.0):
    """
    Wysyła zapytanie A2S do serwera i zwraca odpowiedź.
    """
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
    """
    Pobiera listę graczy z serwera wykorzystując zapytanie A2S_PLAYER.
    Zwraca listę słowników: [{'name': str, 'score': int, 'duration': float}, ...]
    """
    address = (ip, port)
    # Najpierw uzyskujemy challenge
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

def main():
    global previous_player_counts

    debug_log("Start programu. Próba połączenia z BattleMetrics API i serwerami GMod.")
    while True:
        servers = get_polish_gmod_servers()
        if not servers:
            debug_log("Nie znaleziono polskich serwerów GMod. Czekam i ponawiam próbę.")

        for srv in servers:
            srv_id, srv_name, ip, port, server_steam_id = srv
            address_str = f"{ip}:{port}"
            debug_log(f"Przetwarzanie serwera: {srv_id} ({address_str})")

            server_name, player_count, game_mode = get_server_details(srv_id)
            server_filename = get_server_log_filename(srv_id, server_name)
            ensure_server_file_exists(server_filename, server_name, ip, port, game_mode, server_steam_id)

            old_count = previous_player_counts.get(srv_id, None)
            if old_count is None:
                log_to_server_file(server_filename, f"Liczba graczy: {player_count}")
            else:
                diff = player_count - old_count
                if diff > 0:
                    log_to_server_file(server_filename, f"Liczba graczy wzrosła o {diff} do {player_count}")
                elif diff < 0:
                    log_to_server_file(server_filename, f"Liczba graczy zmniejszyła się o {abs(diff)} do {player_count}")
                else:
                    log_to_server_file(server_filename, f"Liczba graczy bez zmian: {player_count}")

            # Pobieramy listę graczy z A2S
            players = get_a2s_player_list(ip, port)
            if players:
                log_to_server_file(server_filename, "Lista aktualnych graczy:")
                for p in players:
                    # Wypisujemy tylko nazwę gracza, można też score i duration
                    log_to_server_file(server_filename, f" - {p['name']} (score: {p['score']}, time: {p['duration']:.1f}s)")
            else:
                log_to_server_file(server_filename, "Brak danych o graczach lub serwer nie odpowiada na A2S_PLAYER.")

            previous_player_counts[srv_id] = player_count

        debug_log("Zakończono iterację, czekam przed kolejną próbą...")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
