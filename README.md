# 9anime Downloader

Command-line Python tool to download episodes from 9animetv using `ffmpeg`.

It supports:

- sub and dub streams
- automatic server fallback
- single episode and batch range downloads
- optional parallel HLS segment downloading (`--parallel-segments`)
- direct authorized m3u8 downloads (`--m3u8-url`)

## Quick Start

### 1. Requirements

- Python 3.12+
- `ffmpeg` installed and available in `PATH`

Check tools:

```bash
python --version
ffmpeg -version
```

### 2. Install dependencies

From this project folder:

```bash
pip install -e .
```

Or install directly:

```bash
pip install requests beautifulsoup4
```

### 3. Run

Single episode:

```bash
python main.py "https://9animetv.to/watch/vinland-saga-40?ep=1144"
```

## Usage

```bash
python main.py "<URL>" [--type sub|dub] [--range START-END] [--out OUTPUT_DIR]
```

Parallel segment mode:

```bash
python main.py "<URL>" --parallel-segments [--workers 8]
```

Direct authorized m3u8 mode:

```bash
python main.py --m3u8-url "<M3U8_URL>" --name "my-video" --out downloads --parallel-segments --workers 12
```

### Arguments

| Argument | Description | Default |
| --- | --- | --- |
| `url` | Episode URL or base watch URL | Required |
| `--type` | Stream type: `sub` or `dub` | `sub` |
| `--range` | Episode range for batch mode, example: `1-12` | None |
| `--out` | Output directory for downloaded files | `downloads` |
| `--parallel-segments` | Download HLS segments concurrently and remux | Off |
| `--workers` | Number of concurrent segment workers in parallel mode | `8` |
| `--m3u8-url` | Direct authorized m3u8 URL (bypasses episode extraction) | None |
| `--name` | Output file name in direct m3u8 mode | `video` |

### Examples

Download a single dub episode:

```bash
python main.py "https://9animetv.to/watch/vinland-saga-40?ep=1144" --type dub
```

Download a range (batch mode):

```bash
python main.py "https://9animetv.to/watch/vinland-saga-40" --range 1-12
```

Download with parallel segments:

```bash
python main.py "https://9animetv.to/watch/vinland-saga-40?ep=1144" --type dub --parallel-segments --workers 12
```

Download from a direct authorized HLS link:

```bash
python main.py --m3u8-url "https://example.com/path/master.m3u8" --name "episode-01" --parallel-segments --workers 12
```

Save to custom folder:

```bash
python main.py "https://9animetv.to/watch/vinland-saga-40?ep=1144" --out downloads/vinland
```

## How It Works

For each episode, the script:

1. fetches available sub/dub servers
2. tries each server in order until one succeeds
3. resolves embed source and extracts the stream URL
4. hands the HLS stream to `ffmpeg` for download/remux

If one server fails, it automatically falls back to the next.

## Output Naming

Files are saved like:

```text
anime-title - EP episode_id [sub|dub].mp4
```

Invalid filename characters are stripped automatically.

## Troubleshooting

- `ffmpeg not found`
	Add `ffmpeg` to `PATH` and restart your terminal.

- `No sub/dub servers found`
	The episode may not have that stream type available.

- `All servers failed`
	9anime server links and APIs can change. Retry later or update extraction logic.

- `Encrypted HLS playlist detected; use non-parallel ffmpeg mode`
	The playlist uses encryption metadata; rerun without `--parallel-segments`.

- HTTP or parsing errors
	Temporary site/API changes or network restrictions may be the cause.

## Notes

- This project depends on third-party endpoints that may change without notice.
- Download reliability can vary by region, server, and time.

## Disclaimer

This project is for educational use.

You are responsible for complying with copyright laws and the website's terms of service in your region.

