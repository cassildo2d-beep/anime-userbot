import os
import asyncio
import aiohttp
import re
import uuid
import gdown
from urllib.parse import urljoin, urlparse

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
# GOOGLE DRIVE
# =====================================================

def extract_drive_folder_id(url):
    match = re.search(r'folders/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    raise Exception("Folder ID do Google Drive não encontrado.")


async def process_drive_folder(folder_id, progress_callback=None):

    items = gdown._list_folder(folder_id)

    results = []

    for item in items:

        name = item["name"]
        file_id = item["id"]
        mime = item["mimeType"]

        # Se for pasta → entrar nela
        if mime == "application/vnd.google-apps.folder":

            sub = await process_drive_folder(file_id, progress_callback)
            results.extend(sub)

        else:

            url = f"https://drive.google.com/uc?id={file_id}"
            output_path = os.path.join(DOWNLOAD_DIR, name)

            loop = asyncio.get_event_loop()

            await loop.run_in_executor(
                None,
                lambda: gdown.download(url, output_path, quiet=False)
            )

            results.append(output_path)

    return results


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
# FALLBACK UNIVERSAL
# =====================================================

async def download_with_ytdlp(url):

    output_template = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-o", output_template,
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

    # GOOGLE DRIVE
    if "drive.google.com" in url_lower and "folders" in url_lower:

        folder_id = extract_drive_folder_id(url)

        return await process_drive_folder(folder_id, progress_callback)

    # EXTENSÃO DIRETA
    if url_lower.endswith(".m3u8"):
        return await download_m3u8(url)

    if url_lower.endswith((".mp4", ".mkv")):
        return await download_direct(url, progress_callback)

    # TESTE HEAD
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

    # TESTE HTML
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

                            result = await process_link(video_url, progress_callback)

                            results.append(result)

                        return results

    except:
        pass

    # FALLBACK FINAL
    return await download_with_ytdlp(url)
