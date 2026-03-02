import os
import asyncio
import aiohttp
import re
import uuid
import json

DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 4 * 1024 * 1024
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".m3u8")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# =============================
# ORDENAÇÃO NATURAL
# =============================

def natural_sort_key(s):
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r'([0-9]+)', s)
    ]


# =============================
# DOWNLOAD DIRETO
# =============================

async def download_direct(url, progress_callback=None):

    timeout = aiohttp.ClientTimeout(total=None)

    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
        async with session.get(url, allow_redirects=True) as resp:

            if resp.status != 200:
                raise Exception(f"Erro HTTP {resp.status}")

            filename = os.path.basename(url.split("?")[0])

            if not filename or "." not in filename:
                filename = str(uuid.uuid4()) + ".mp4"

            output_path = os.path.join(DOWNLOAD_DIR, filename)

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            last_percent = 0

            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    f.write(chunk)
                    downloaded += len(chunk)

                    if total and progress_callback:
                        percent = (downloaded / total) * 100
                        if percent - last_percent >= 10:
                            last_percent = percent
                            await progress_callback(percent)

    return output_path


# =============================
# DOWNLOAD M3U8
# =============================

async def download_m3u8(url):

    filename = str(uuid.uuid4()) + ".mp4"
    output_path = os.path.join(DOWNLOAD_DIR, filename)

    cmd = [
        "ffmpeg",
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-y",
        output_path
    ]

    process = await asyncio.create_subprocess_exec(*cmd)
    await process.wait()

    if process.returncode != 0:
        raise Exception("Erro ao converter m3u8.")

    return output_path


# =============================
# PROCESS LINK UNIVERSAL
# =============================

async def process_link(url, progress_callback=None):

    url_lower = url.lower()

    # EXTENSÃO DIRETA
    if url_lower.endswith(".m3u8"):
        return await download_m3u8(url)

    if url_lower.endswith((".mp4", ".mkv")):
        return await download_direct(url, progress_callback)

    # =============================
    # TENTA LISTAR COM YT-DLP
    # =============================

    try:
        cmd = [
            "yt-dlp",
            "-J",
            "--flat-playlist",
            url
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0 and stdout:

            data = json.loads(stdout.decode())
            entries = data.get("entries")

            if entries:

                video_urls = []

                for entry in entries:
                    entry_url = entry.get("url")
                    if entry_url:
                        video_urls.append(entry_url)

                if video_urls:

                    video_urls.sort(key=natural_sort_key)
                    results = []

                    for video_url in video_urls:
                        result = await process_link(video_url, progress_callback)
                        results.append(result)

                    return results

    except Exception:
        pass

    # =============================
    # FALLBACK YT-DLP NORMAL
    # =============================

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-o", os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        url
    ]

    process = await asyncio.create_subprocess_exec(*cmd)
    await process.wait()

    if process.returncode != 0:
        raise Exception("Erro ao baixar com yt-dlp.")

    files = sorted(
        [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)],
        key=os.path.getctime
    )

    return files[-1]
