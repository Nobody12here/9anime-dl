import requests
import subprocess
import argparse
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
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


def render_segment_progress(
    filename,
    completed,
    total,
    downloaded_bytes,
    start_time,
    done_seconds,
    total_seconds,
):
    width = 28
    ratio = completed / total if total else 0
    filled = int(width * min(max(ratio, 0.0), 1.0))
    bar = "#" * filled + "-" * (width - filled)
    percent = f"{ratio * 100:6.2f}%"

    elapsed = max(time.time() - start_time, 0.001)
    mbps = (downloaded_bytes / 1024 / 1024) / elapsed

    if total_seconds:
        time_part = f"{format_seconds(done_seconds)}/{format_seconds(total_seconds)}"
    else:
        time_part = f"{completed}/{total} seg"

    message = f"\r[{bar}] {percent} {time_part} | {mbps:.2f} MB/s {filename}"
    sys.stdout.write(message[:140])
    sys.stdout.flush()


def get_media_playlist(m3u8_url, headers=None):
    headers = headers or {"User-Agent": "Mozilla/5.0"}
    res = requests.get(m3u8_url, headers=headers, timeout=30)
    res.raise_for_status()

    lines = [line.strip() for line in res.text.splitlines() if line.strip()]

    # Handle master playlist by selecting the highest BANDWIDTH variant.
    variants = []
    for idx, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:") and idx + 1 < len(lines):
            bw_match = re.search(r"BANDWIDTH=(\d+)", line)
            bandwidth = int(bw_match.group(1)) if bw_match else 0
            next_line = lines[idx + 1]
            if not next_line.startswith("#"):
                variants.append((bandwidth, urljoin(m3u8_url, next_line)))

    if variants:
        _, chosen = max(variants, key=lambda x: x[0])
        return get_media_playlist(chosen, headers=headers)

    if any("#EXT-X-KEY:" in line and "METHOD=NONE" not in line for line in lines):
        raise ValueError("Encrypted HLS playlist detected; use non-parallel ffmpeg mode")

    segments = []
    durations = []
    current_duration = 0.0

    for line in lines:
        if line.startswith("#EXTINF:"):
            try:
                current_duration = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                current_duration = 0.0
        elif not line.startswith("#"):
            segments.append(urljoin(m3u8_url, line))
            durations.append(current_duration)
            current_duration = 0.0

    if not segments:
        raise ValueError("No media segments found in playlist")

    return segments, durations


def download_segment(url, destination, headers, retries=4):
    last_error = None
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            with open(destination, "wb") as f:
                f.write(response.content)
            return os.path.getsize(destination)
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))

    raise RuntimeError(f"Failed segment: {url} ({last_error})")


def remux_to_mp4(input_path, output_path):
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-c",
        "copy",
        output_path,
    ]
    subprocess.run(cmd, check=True)


def parallel_download_hls(m3u8, output, headers=None, workers=8):
    headers = headers or {"User-Agent": "Mozilla/5.0"}
    segments, durations = get_media_playlist(m3u8, headers=headers)
    total_segments = len(segments)
    total_seconds = sum(durations) if any(durations) else None

    completed = 0
    downloaded_bytes = 0
    done_seconds = 0.0
    start_time = time.time()

    with tempfile.TemporaryDirectory(prefix="hls_parts_") as tmp_dir:
        part_paths = [os.path.join(tmp_dir, f"{idx:06d}.ts") for idx in range(total_segments)]

        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(download_segment, url, part_paths[idx], headers): idx
                for idx, url in enumerate(segments)
            }

            for future in as_completed(futures):
                idx = futures[future]
                size = future.result()
                completed += 1
                downloaded_bytes += size
                done_seconds += durations[idx] if idx < len(durations) else 0.0
                render_segment_progress(
                    os.path.basename(output),
                    completed,
                    total_segments,
                    downloaded_bytes,
                    start_time,
                    done_seconds,
                    total_seconds,
                )

        merged_path = os.path.join(tmp_dir, "merged.ts")
        with open(merged_path, "wb") as merged:
            for part_path in part_paths:
                with open(part_path, "rb") as part_file:
                    merged.write(part_file.read())

        clear_progress_line()
        remux_to_mp4(merged_path, output)


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
def download(m3u8, output, use_parallel=False, workers=8, headers=None):
    if use_parallel:
        parallel_download_hls(m3u8, output, headers=headers, workers=workers)
        return

    total_seconds = parse_m3u8_duration(m3u8)

    ffmpeg_headers = headers or {
        "Referer": "https://rapid-cloud.co/",
        "User-Agent": "Mozilla/5.0",
    }
    header_value = "\r\n".join([f"{k}: {v}" for k, v in ffmpeg_headers.items()])

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-nostats",
        "-headers",
        header_value,
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
                render_progress(
                    os.path.basename(output), current_seconds, total_seconds, speed
                )
            elif line.startswith("speed="):
                speed = line.split("=", 1)[1].strip()
            elif line == "progress=end":
                if total_seconds:
                    render_progress(
                        os.path.basename(output), total_seconds, total_seconds, speed
                    )

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
def download_episode(ep_url, typ, out_dir, use_parallel=False, workers=8):
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
            download(
                hls,
                filepath,
                use_parallel=use_parallel,
                workers=workers,
                headers={"Referer": "https://rapid-cloud.co/", "User-Agent": "Mozilla/5.0"},
            )

            print(f"[✅] Downloaded: {filename}")
            return

        except Exception as e:
            print(f"[!] Failed on {server['name']}: {e}")
            continue

    print("[-] All servers failed")


# -----------------------------
# Batch Mode
# -----------------------------
def download_range(base_url, start, end, typ, out_dir, use_parallel=False, workers=8):
    for ep in range(start, end + 1):
        url = f"{base_url}?ep={ep}"
        download_episode(url, typ, out_dir, use_parallel=use_parallel, workers=workers)


# -----------------------------
# CLI
# -----------------------------
def main():
    parser = argparse.ArgumentParser("Anime Downloader")

    parser.add_argument("url", nargs="?", help="Episode or base URL")
    parser.add_argument("--type", choices=["sub", "dub"], default="sub")
    parser.add_argument("--range", help="Episode range (e.g. 1-12)")
    parser.add_argument("--out", default="downloads")
    parser.add_argument("--parallel-segments", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--m3u8-url", help="Direct authorized HLS URL")
    parser.add_argument("--name", default="video", help="Output filename for --m3u8-url")

    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if args.m3u8_url:
        output_name = args.name if args.name.lower().endswith(".mp4") else f"{args.name}.mp4"
        output_path = os.path.join(args.out, sanitize(output_name))
        print(f"[+] Downloading direct m3u8 to: {output_path}")
        download(
            args.m3u8_url,
            output_path,
            use_parallel=args.parallel_segments,
            workers=args.workers,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        print(f"[✅] Downloaded: {os.path.basename(output_path)}")
        return

    if not args.url:
        parser.error("url is required unless --m3u8-url is provided")

    if args.range:
        start, end = map(int, args.range.split("-"))
        download_range(
            args.url,
            start,
            end,
            args.type,
            args.out,
            use_parallel=args.parallel_segments,
            workers=args.workers,
        )
    else:
        download_episode(
            args.url,
            args.type,
            args.out,
            use_parallel=args.parallel_segments,
            workers=args.workers,
        )


if __name__ == "__main__":
    main()
