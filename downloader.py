import os
import asyncio
import aiohttp
import re
import uuid
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 4 * 1024 * 1024
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".m3u8")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =====================================================
# ORDENAÇÃO NATURAL (ep1, ep2, ep10 correto)
# =====================================================

def natural_sort_key(s):
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r'([0-9]+)', s)
    ]

# =====================================================
# EXTRAÇÃO ANIMEFIRE
# =====================================================

async def extract_animefire_video(url):

    match = re.search(r"/animes/([^/]+)/(\d+)", url)

    if not match:
        raise Exception("Link AnimeFire inválido")

    anime = match.group(1)
    episode = match.group(2)

    download_page = f"https://animefire.plus/download/{anime}/{episode}"

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(download_page) as resp:

            if resp.status != 200:
                raise Exception("Não foi possível acessar página de download")

            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")

    qualities = {}

    for a in soup.find_all("a", href=True):

        text = a.text.strip().lower()

        if "full" in text:
            qualities["full"] = a["href"]

        elif "hd" in text:
            qualities["hd"] = a["href"]

        elif "sd" in text:
            qualities["sd"] = a["href"]

    if not qualities:
        raise Exception("Nenhum vídeo encontrado no AnimeFire")

    for q in ["full", "hd", "sd"]:
        if q in qualities:
            return qualities[q]

    return list(qualities.values())[0]

# =====================================================
# EXTRAIR VÍDEOS DE PASTA HTML
# =====================================================

async def extract_all_videos_from_folder(url):

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url) as resp:

            if resp.status != 200:
                raise Exception("Não foi possível acessar a pasta.")

            content_type = resp.headers.get("content-type", "")

            if "text/html" not in content_type:
                raise Exception("Link não é uma pasta HTML.")

            html = await resp.text(encoding="utf-8", errors="ignore")

    links = re.findall(r'href="([^"]+)"', html)

    video_links = []

    for link in links:

        if link.lower().endswith(VIDEO_EXTENSIONS):

            full_link = urljoin(url, link)
            video_links.append(full_link)

    if not video_links:
        raise Exception("Nenhum vídeo encontrado na pasta.")

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

            content_type = resp.headers.get("content-type", "")

            if "text/html" in content_type:
                raise Exception("Servidor retornou HTML inesperado.")

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

    return output_path

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

    return output_path

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

    if not files:
        raise Exception("yt-dlp não gerou arquivo.")

    return files[-1]

# =====================================================
# FUNÇÃO PRINCIPAL
# =====================================================

async def process_link(url, progress_callback=None):

    url_lower = url.lower()

    # =====================================================
    # DETECTAR ANIMEFIRE
    # =====================================================

    if "animefire" in url_lower:

        video_url = await extract_animefire_video(url)

        return await download_direct(video_url, progress_callback)

    # =====================================================
    # EXTENSÃO DIRETA
    # =====================================================

    if url_lower.endswith(".m3u8"):
        return await download_m3u8(url)

    if url_lower.endswith((".mp4", ".mkv")):
        return await download_direct(url, progress_callback)

    # =====================================================
    # TESTE HEAD
    # =====================================================

    try:

        async with aiohttp.ClientSession(headers=HEADERS) as session:

            async with session.head(url, allow_redirects=True) as resp:

                content_type = resp.headers.get("content-type", "")
                content_disp = resp.headers.get("content-disposition", "")

                if (
                    "video" in content_type
                    or "octet-stream" in content_type
                    or "attachment" in content_disp
                ):

                    return await download_direct(url, progress_callback)

    except:
        pass

    # =====================================================
    # TESTE HTML
    # =====================================================

    try:

        async with aiohttp.ClientSession(headers=HEADERS) as session:

            async with session.get(url) as resp:

                content_type = resp.headers.get("content-type", "")

                if any(x in content_type for x in ["video", "octet-stream"]):

                    return await download_direct(url, progress_callback)

                if "text/html" in content_type:

                    html = await resp.text(encoding="utf-8", errors="ignore")

                    if any(ext in html.lower() for ext in VIDEO_EXTENSIONS):

                        video_links = await extract_all_videos_from_folder(url)

                        results = []

                        for video_url in video_links:

                            result = await process_link(
                                video_url,
                                progress_callback
                            )

                            results.append(result)

                        return results

    except:
        pass

    # =====================================================
    # FALLBACK FINAL
    # =====================================================

    return await download_with_ytdlp(url)
