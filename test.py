import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}
BASE_URL = "https://9animetv.to"
SEASON_ID = 40  # we can get this from when the end of the url of the anime without parameter like https://9animetv.to/watch/jujutsu-kaisen-the-culling-game-part-1-20401 in this 20401 is the SEASON_ID or ANIME_ID
res = requests.get(
    f"https://9animetv.to/ajax/episode/list/{SEASON_ID}", headers=HEADERS
)
res.raise_for_status()
html_content = res.json()["html"]

soup = BeautifulSoup(html_content, "html.parser")
episodes_list = soup.find("div", class_="episodes-ul")

result = [
    {
        "id":episode.attrs.get("data-id"),
        "episode":episode.attrs.get("data-number"),
        "title": episode.attrs.get("title"),
        "link": f"{BASE_URL}{episode.attrs.get("href")}",
    }
    for episode in episodes_list.find_all("a")
]
print(result)
