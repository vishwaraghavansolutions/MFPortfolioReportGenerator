import requests
import streamlit as st

def browser_like_download(url, chunk_size=1024):
    """
    Downloads a PDF using browser-like headers and shows a Streamlit progress bar.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,application/xhtml+xml,"
                  "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.niftyindices.com/",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }

    with st.spinner("Connecting to Nifty Indices server..."):
        response = requests.get(url, headers=headers, stream=True, timeout=60)

    if response.status_code != 200:
        st.error(f"Failed to download PDF. Status: {response.status_code}")
        return None

    total_size = int(response.headers.get("Content-Length", 0))
    progress = st.progress(0)
    downloaded = 0
    pdf_bytes = b""

    for chunk in response.iter_content(chunk_size=chunk_size):
        if chunk:
            pdf_bytes += chunk
            downloaded += len(chunk)
            if total_size > 0:
                progress.progress(min(downloaded / total_size, 1.0))

    progress.progress(1.0)
    st.success("Download complete!")

    return pdf_bytes