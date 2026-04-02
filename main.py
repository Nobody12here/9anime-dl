import requests
import subprocess
import argparse
import os
import re
import sys
from bs4 import BeautifulSoup


BASE = "https://9animetv.to"

HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}


# -----------------------------
# Utils
# -----------------------------
def sanitize(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)


def extract_episode_id(url):
    return url.split("ep=")[-1]


def extract_title_slug(url):
    return url.split("/watch/")[1].split("?")[0]


# -----------------------------
# Step 1: Servers
# -----------------------------
def get_servers(episode_id):
    url = f"{BASE}/ajax/episode/servers?episodeId={episode_id}"
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()

    soup = BeautifulSoup(res.json()["html"], "html.parser")

    result = {"sub": [], "dub": []}

    for section_type in ["servers-sub", "servers-dub"]:
        section = soup.find("div", class_=section_type)
        if not section:
            continue

        typ = "dub" if "dub" in section_type else "sub"

        for item in section.find_all("div", class_="server-item"):
            result[typ].append({"id": item["data-id"], "name": item.text.strip()})

    return result


# -----------------------------
# Step 2: Embed
# -----------------------------
def get_embed(server_id, referer):
    url = f"{BASE}/ajax/episode/sources?id={server_id}"

    headers = HEADERS.copy()
    headers["Referer"] = referer

    res = requests.get(url, headers=headers)
    res.raise_for_status()

    return res.json().get("link")


# -----------------------------
# Step 3: Extract ID
# -----------------------------
def extract_embed_id(url):
    return url.split("/")[-1].split("?")[0]


def parse_m3u8_duration(m3u8_url):
    try:
        res = requests.get(m3u8_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        res.raise_for_status()
    except Exception:
        return None

    total = 0.0
    found = False

    for line in res.text.splitlines():
        if line.startswith("#EXTINF:"):
            found = True
            try:
                total += float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                return None

    return total if found and total > 0 else None


def format_seconds(seconds):
    seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"

    return f"{minutes:02d}:{remaining_seconds:02d}"


def render_progress(filename, current_seconds, total_seconds, speed=None):
    width = 28

    if total_seconds:
        ratio = min(max(current_seconds / total_seconds, 0.0), 1.0)
        filled = int(width * ratio)
        bar = "#" * filled + "-" * (width - filled)
        percent = f"{ratio * 100:6.2f}%"
        elapsed = format_seconds(current_seconds)
        total = format_seconds(total_seconds)
        speed_text = f" | {speed}" if speed else ""
        message = f"\r[{bar}] {percent} {elapsed}/{total}{speed_text} {filename}"
    else:
        spinner = "|/-\\"
        frame = int(current_seconds) % len(spinner)
        speed_text = f" | {speed}" if speed else ""
        message = f"\r[{spinner[frame]}] {format_seconds(current_seconds)}{speed_text} {filename}"

    sys.stdout.write(message[:120])
    sys.stdout.flush()


def clear_progress_line():
    sys.stdout.write("\r" + " " * 140 + "\r")
    sys.stdout.flush()


# -----------------------------
# Step 4: HLS
# -----------------------------
def get_hls(embed_id):
    url = f"https://rapid-cloud.co/embed-2/v2/e-1/getSources?id={embed_id}"

    res = requests.get(
        url, headers={"Referer": "https://rapid-cloud.co/", "User-Agent": "Mozilla/5.0"}
    )

    res.raise_for_status()

    return res.json()["sources"][0]["file"]


# -----------------------------
# Step 5: ffmpeg
# -----------------------------
def download(m3u8, output):
    total_seconds = parse_m3u8_duration(m3u8)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-nostats",
        "-headers",
        "Referer: https://rapid-cloud.co/\r\nUser-Agent: Mozilla/5.0",
        "-allowed_extensions",
        "ALL",
        "-http_persistent",
        "1",
        "-multiple_requests",
        "1",
        "-rw_timeout",
        "15000000",
        "-progress",
        "pipe:1",
        "-protocol_whitelist",
        "file,http,https,tcp,tls",
        "-i",
        m3u8,
        "-c",
        "copy",
        output,
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    current_seconds = 0.0
    speed = None

    try:
        if process.stdout is None:
            raise RuntimeError("ffmpeg progress stream is unavailable")

        for line in process.stdout:
            line = line.strip()

            if line.startswith("out_time="):
                value = line.split("=", 1)[1]
                hours, minutes, rest = value.split(":")
                seconds = float(rest)
                current_seconds = int(hours) * 3600 + int(minutes) * 60 + seconds
                render_progress(os.path.basename(output), current_seconds, total_seconds, speed)
            elif line.startswith("speed="):
                speed = line.split("=", 1)[1].strip()
            elif line == "progress=end":
                if total_seconds:
                    render_progress(os.path.basename(output), total_seconds, total_seconds, speed)

        returncode = process.wait()
        clear_progress_line()

        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, cmd)

    finally:
        if process.stdout is not None:
            process.stdout.close()


# -----------------------------
# Core Episode Downloader
# -----------------------------
def download_episode(ep_url, typ, out_dir):
    ep_id = extract_episode_id(ep_url)
    slug = extract_title_slug(ep_url)

    print(f"\n[*] Episode ID: {ep_id}")

    servers = get_servers(ep_id)

    chosen_list = servers.get(typ)

    if not chosen_list:
        print(f"[-] No {typ} servers found")
        return

    referer = ep_url

    for server in chosen_list:
        try:
            print(f"[+] Trying server: {server['name']}")

            embed = get_embed(server["id"], referer)
            embed_id = extract_embed_id(embed)
            hls = get_hls(embed_id)
            print(hls)
            filename = sanitize(f"{slug} - EP {ep_id} [{typ}].mp4")
            filepath = os.path.join(out_dir, filename)

            print(f"[+] Downloading: {filename}")
            download(hls, filepath)

            print(f"[✅] Downloaded: {filename}")
            return

        except Exception as e:
            print(f"[!] Failed on {server['name']}: {e}")
            continue

    print("[-] All servers failed")


# -----------------------------
# Batch Mode
# -----------------------------
def download_range(base_url, start, end, typ, out_dir):
    for ep in range(start, end + 1):
        url = f"{base_url}?ep={ep}"
        download_episode(url, typ, out_dir)


# -----------------------------
# CLI
# -----------------------------
def main():
    parser = argparse.ArgumentParser("Anime Downloader")

    parser.add_argument("url", help="Episode or base URL")
    parser.add_argument("--type", choices=["sub", "dub"], default="sub")
    parser.add_argument("--range", help="Episode range (e.g. 1-12)")
    parser.add_argument("--out", default="downloads")

    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if args.range:
        start, end = map(int, args.range.split("-"))
        download_range(args.url, start, end, args.type, args.out)
    else:
        download_episode(args.url, args.type, args.out)


if __name__ == "__main__":
    main()
