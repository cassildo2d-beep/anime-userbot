import os
import asyncio
import aiohttp
import re
import uuid
import subprocess
from urllib.parse import urljoin, urlparse

import subliminal
from babelfish import Language

DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 4 * 1024 * 1024
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".m3u8")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =====================================================
# ORDENAÇÃO NATURAL
# =====================================================

def natural_sort_key(s):
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r'([0-9]+)', s)
    ]


# =====================================================
# SOFTSUB (MUX LEGENDA)
# =====================================================

def softsub(video_path):

    try:

        video = subliminal.Video.fromname(video_path)

        subtitles = subliminal.download_best_subtitles(
            [video],
            {Language("por", "BR")}
        )

        subliminal.save_subtitles(video, subtitles[video])

        folder = os.path.dirname(video_path)

        subtitle = None

        for f in os.listdir(folder):
            if f.endswith(".srt"):
                subtitle = os.path.join(folder, f)

        if not subtitle:
            return video_path

        output = video_path.replace(".mkv", "_sub.mkv").replace(".mp4", "_sub.mkv")

        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-i", subtitle,
            "-map", "0",
            "-map", "1",
            "-c", "copy",
            "-c:s", "srt",
            "-metadata:s:s:0", "language=por",
            output
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(output):
            os.remove(video_path)
            os.remove(subtitle)
            return output

        return video_path

    except:
        return video_path


# =====================================================
# TORRENT DOWNLOAD
# =====================================================

async def download_torrent(url):

    folder = os.path.join(DOWNLOAD_DIR, str(uuid.uuid4()))
    os.makedirs(folder, exist_ok=True)

    cmd = [
        "aria2c",
        "--dir", folder,
        "--seed-time=0",
        "--max-connection-per-server=16",
        "--split=16",
        url
    ]

    process = await asyncio.create_subprocess_exec(*cmd)
    await process.wait()

    files = []

    for root, _, filenames in os.walk(folder):
        for f in filenames:
            if f.lower().endswith((".mp4", ".mkv")):
                files.append(os.path.join(root, f))

    if not files:
        raise Exception("Torrent baixado mas nenhum vídeo encontrado.")

    files.sort(key=natural_sort_key)

    if len(files) == 1:
        return softsub(files[0])

    return [softsub(f) for f in files]


# =====================================================
# EXTRAIR VÍDEOS DE PASTA HTML
# =====================================================

async def extract_all_videos_from_folder(url):

    async with aiohttp.ClientSession(headers=HEADERS) as session:

        async with session.get(url) as resp:

            if resp.status != 200:
                raise Exception("Não foi possível acessar a pasta.")

            html = await resp.text(encoding="utf-8", errors="ignore")

    links = re.findall(r'href="([^"]+)"', html)

    video_links = []

    for link in links:
        if link.lower().endswith(VIDEO_EXTENSIONS):
            full_link = urljoin(url, link)
            video_links.append(full_link)

    if not video_links:
        raise Exception("Nenhum vídeo encontrado.")

    video_links.sort(key=natural_sort_key)

    return video_links


# =====================================================
# DOWNLOAD DIRETO
# =====================================================

async def download_direct(url, progress_callback=None):

    timeout = aiohttp.ClientTimeout(total=None)

    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:

        async with session.get(url, allow_redirects=True) as resp:

            if resp.status != 200:
                raise Exception(f"Erro HTTP {resp.status}")

            filename = None

            content_disposition = resp.headers.get("Content-Disposition")

            if content_disposition:
                match = re.findall('filename="?([^"]+)"?', content_disposition)
                if match:
                    filename = match[0]

            if not filename:
                parsed = urlparse(str(resp.url))
                filename = os.path.basename(parsed.path)

            if not filename or "." not in filename:
                filename = str(uuid.uuid4()) + ".mp4"

            output_path = os.path.join(DOWNLOAD_DIR, filename)

            total = int(resp.headers.get("content-length", 0) or 0)
            downloaded = 0
            last_percent = 0

            with open(output_path, "wb") as f:

                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):

                    f.write(chunk)

                    downloaded += len(chunk)

                    if total and progress_callback:

                        percent = (downloaded / total) * 100

                        if percent - last_percent >= 5:
                            last_percent = percent
                            await progress_callback(round(percent, 1))

    return softsub(output_path)


# =====================================================
# DOWNLOAD M3U8
# =====================================================

async def download_m3u8(url):

    filename = str(uuid.uuid4()) + ".mp4"

    output_path = os.path.join(DOWNLOAD_DIR, filename)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )

    await process.wait()

    if process.returncode != 0:
        raise Exception("Erro ao converter m3u8.")

    return softsub(output_path)


# =====================================================
# FALLBACK YT-DLP
# =====================================================

async def download_with_ytdlp(url):

    output_template = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-o",
        output_template,
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

    return softsub(files[-1])


# =====================================================
# FUNÇÃO PRINCIPAL
# =====================================================

async def process_link(url, progress_callback=None):

    url_lower = url.lower()

    if url_lower.startswith("magnet:") or url_lower.endswith(".torrent"):
        return await download_torrent(url)

    if url_lower.endswith(".m3u8"):
        return await download_m3u8(url)

    if url_lower.endswith((".mp4", ".mkv")):
        return await download_direct(url, progress_callback)

    return await download_with_ytdlp(url)
