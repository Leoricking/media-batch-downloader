import glob
import os
import re
from typing import Optional


EXCLUDE_FILES = {
    "download_link.txt",
    "undownload_link.txt",
    "ig_links.txt",
    "fb_links.txt",
}


def get_input_file(base_dir: str = ".", preferred_file: Optional[str] = None) -> Optional[str]:
    if preferred_file:
        if os.path.isabs(preferred_file):
            if os.path.exists(preferred_file):
                return preferred_file
        else:
            candidate = os.path.join(base_dir, preferred_file)
            if os.path.exists(candidate):
                return candidate

    txt_files = glob.glob(os.path.join(base_dir, "*.txt"))
    available_files = [
        f for f in txt_files
        if os.path.basename(f) not in EXCLUDE_FILES
    ]

    if not available_files:
        return None

    if len(available_files) == 1:
        return available_files[0]

    available_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return available_files[0]


def extract_urls(content: str) -> list[str]:
    urls = re.findall(r'https?://[^\s,，。]+', content)
    urls = [u.strip().strip("，").strip("。") for u in urls]
    return list(dict.fromkeys(urls))


def is_instagram_media(url: str) -> bool:
    u = url.lower()
    return "instagram.com" in u and (
        "/p/" in u or
        "/reel/" in u or
        "/reels/" in u
    )


def is_facebook_media(url: str) -> bool:
    u = url.lower()

    is_fb_domain = (
        "facebook.com" in u or
        "fb.watch" in u or
        "m.facebook.com" in u or
        "www.facebook.com" in u
    )
    if not is_fb_domain:
        return False

    fb_patterns = [
        "/share/",
        "/share/r/",
        "/share/v/",
        "/watch/",
        "/watch?",
        "/reel/",
        "/videos/",
        "/posts/",
        "/story.php",
        "fb.watch/",
        "v=",
        "fbid=",
    ]

    return any(p in u for p in fb_patterns)


def sort_links(
    input_file: Optional[str] = None,
    base_dir: str = ".",
    output_dir: Optional[str] = None,
):
    input_path = get_input_file(base_dir=base_dir, preferred_file=input_file)
    if not input_path:
        raise FileNotFoundError("目錄下找不到任何可處理的 .txt 檔案。")

    if output_dir is None:
        output_dir = os.path.join(base_dir, "output")

    os.makedirs(output_dir, exist_ok=True)

    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read()

    urls = extract_urls(content)

    download_links = []
    undownload_links = []

    for url in urls:
        if is_instagram_media(url) or is_facebook_media(url):
            download_links.append(url)
        else:
            undownload_links.append(url)

    download_path = os.path.join(output_dir, "download_link.txt")
    undownload_path = os.path.join(output_dir, "undownload_link.txt")

    with open(download_path, "w", encoding="utf-8") as f:
        f.write("\n".join(download_links))

    with open(undownload_path, "w", encoding="utf-8") as f:
        f.write("\n".join(undownload_links))

    stats = {
        "total_urls": len(urls),
        "downloadable": len(download_links),
        "undownloadable": len(undownload_links),
    }

    return input_path, download_path, undownload_path, stats


def main():
    try:
        input_file, download_path, undownload_path, stats = sort_links()
        print("-" * 45)
        print(f"處理檔案: {input_file}")
        print("結果已儲存：")
        print(f"✅ 可下載: {download_path} ({stats['downloadable']} 筆)")
        print(f"❌ 不可下載: {undownload_path} ({stats['undownloadable']} 筆)")
        print("-" * 45)
    except Exception as e:
        print(f"執行失敗: {e}")


if __name__ == "__main__":
    main()