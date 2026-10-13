"""Read public QQ artist metadata and verify hot/latest order across two pages."""

import argparse
import asyncio
import json
from dataclasses import replace

from qqmusic_api import Client


async def probe(mid: str, page_size: int) -> None:
    """Compare two pages against a larger prefix without reading account credentials."""
    async with Client() as client:
        for label, order in (("热门", 1), ("最新", 2)):
            pages = []
            for number, begin in ((page_size, 0), (page_size, page_size), (page_size * 2, 0)):
                request = client.singer.get_songs_list(mid)
                request = replace(
                    request,
                    param={"singerMid": mid, "order": order, "num": number, "begin": begin},
                    disable_parse=True,
                )
                async with asyncio.timeout(30):
                    result = await request
                songs = [item["songInfo"] for item in result["songList"]]
                pages.append(songs)
                print(
                    json.dumps(
                        {
                            "singerMid": mid,
                            "order": order,
                            "label": label,
                            "begin": begin,
                            "num": number,
                            "totalNum": result["totalNum"],
                            "songs": [
                                [song["mid"], song["name"], song.get("time_public", "")]
                                for song in songs
                            ],
                        },
                        ensure_ascii=False,
                    )
                )
            first, second, prefix = pages

            def ids(songs: list[dict]) -> list[str]:
                """Extract song identities for pagination comparisons."""
                return [song["mid"] for song in songs]

            if ids(first + second) != ids(prefix):
                raise ValueError(f"{label}分页拼接与完整前缀不一致")
            if set(ids(first)) & set(ids(second)):
                raise ValueError(f"{label}两页存在重复歌曲")
            if order == 2:
                dates = [song.get("time_public", "") for song in prefix]
                if dates != sorted(dates, reverse=True):
                    raise ValueError("最新顺序未按发行日期降序返回")
            print(f"{label}: 分页拼接一致、两页无重复，返回数量 {len(first)}/{len(second)}")


def main() -> None:
    """Accept an exact singer MID and page size for repeatable live verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mid", nargs="?", default="0025NhlN2yWrP4")
    parser.add_argument("--page-size", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(probe(args.mid, args.page_size))


if __name__ == "__main__":
    main()
