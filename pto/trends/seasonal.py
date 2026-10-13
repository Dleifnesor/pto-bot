"""Holiday calendar (posted ahead of time) and evergreen niches."""
from __future__ import annotations

import datetime as dt
import random
from typing import Callable

from . import TrendItem


def easter(year: int) -> dt.date:
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th `weekday` (Mon=0) of a month."""
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def fixed(month: int, day: int) -> Callable[[int], dt.date | None]:
    return lambda y: dt.date(y, month, day)


def table(dates: dict[int, tuple[int, int]]) -> Callable[[int], dt.date | None]:
    return lambda y: dt.date(y, *dates[y]) if y in dates else None


LUNAR_ANIMAL = {2026: "horse", 2027: "goat", 2028: "monkey", 2029: "rooster", 2030: "dog"}

# (name, date function, theme hints)
EVENTS: list[tuple[str, Callable[[int], dt.date | None], str]] = [
    ("New Year", fixed(1, 1), "new year resolutions, fireworks, champagne, fresh start humor"),
    ("Lunar New Year", table({2026: (2, 17), 2027: (2, 6), 2028: (1, 26), 2029: (2, 13), 2030: (2, 3)}), "zodiac animal of the year, red and gold, lanterns"),
    ("Valentine's Day", fixed(2, 14), "love puns, hearts, cute couples, anti-valentine humor, galentines"),
    ("Pi Day", fixed(3, 14), "math puns, pie, nerdy humor"),
    ("St. Patrick's Day", fixed(3, 17), "shamrocks, lucky, green, irish blessing humor"),
    ("First day of spring", fixed(3, 20), "flowers, blossoms, bees, spring cleaning humor"),
    ("Easter", easter, "bunnies, pastel eggs, chicks, spring"),
    ("Earth Day", fixed(4, 22), "save the planet, nature, recycling, plants"),
    ("Teacher Appreciation Week", lambda y: nth_weekday(y, 5, 0, 1), "teacher life humor, apples, classroom"),
    ("Nurses Week", fixed(5, 6), "nurse life humor, coffee and scrubs, healthcare heroes"),
    ("Mother's Day", lambda y: nth_weekday(y, 5, 6, 2), "best mom, mama, plant mom, dog mom, cat mom"),
    ("Graduation season", fixed(5, 20), "class of {year}, graduation cap, proud graduate"),
    ("Pride Month", fixed(6, 1), "love is love, rainbow, inclusive and positive"),
    ("Father's Day", lambda y: nth_weekday(y, 6, 6, 3), "dad jokes, grill dad, best dad, fishing dad"),
    ("First day of summer", fixed(6, 21), "beach, sunshine, ice cream, vacation mode"),
    ("Canada Day", fixed(7, 1), "maple leaf, canadian humor, moose, beavers"),
    ("Fourth of July", fixed(7, 4), "american summer, fireworks, bbq, stars and stripes"),
    ("Back to school", fixed(8, 15), "school supplies, teacher and student humor, first day"),
    ("First day of fall", fixed(9, 22), "pumpkin spice, cozy sweater weather, falling leaves"),
    ("Halloween", fixed(10, 31), "spooky cute ghosts, witches, black cats, pumpkins, skeletons, spooky season"),
    ("Day of the Dead", fixed(11, 1), "sugar skulls, marigolds, colorful calaveras, respectful and festive"),
    ("Bonfire Night", fixed(11, 5), "fireworks, bonfire, cozy autumn night"),
    ("Thanksgiving", lambda y: nth_weekday(y, 11, 3, 4), "turkey humor, grateful, pie, cozy family dinner"),
    ("Hanukkah", table({2026: (12, 4), 2027: (12, 24), 2028: (12, 12), 2029: (12, 1), 2030: (12, 20)}), "menorah, dreidels, latkes, festival of lights"),
    ("First day of winter", fixed(12, 21), "snowflakes, cozy cocoa, penguins, winter solstice"),
    ("Christmas", fixed(12, 25), "cute santa, reindeer, christmas trees, ugly sweater humor, gingerbread, holiday cats and dogs"),
]


def friday_13ths(year: int) -> list[dt.date]:
    return [dt.date(year, m, 13) for m in range(1, 13) if dt.date(year, m, 13).weekday() == 4]


def _after(fn: Callable[[int], dt.date], days: int) -> Callable[[int], dt.date]:
    return lambda y: fn(y) + dt.timedelta(days=days)


_thanksgiving = lambda y: nth_weekday(y, 11, 3, 4)  # noqa: E731

# Pop-culture, fandom, sports-season, sky and "national day" moments. Hints never name leagues, teams, shows or
# trademarked events; the concept step adds the creative angle.
POP_EVENTS: list[tuple[str, Callable[[int], dt.date | list[dt.date] | None], str]] = [
    ("Black Friday", _after(_thanksgiving, 1), "shopping-deal humor, retail worker life, shopaholic cats"),
    ("Cyber Monday", _after(_thanksgiving, 4), "online shopping humor, add-to-cart life, cozy shopping from bed"),
    ("Video game awards night", lambda y: nth_weekday(y, 12, 3, 2), "gamer trophies, game-of-the-year debates, controller and pixel-art humor"),
    ("Movie awards season", lambda y: nth_weekday(y, 3, 6, 2), "film buff life, popcorn, red-carpet humor, movie-night snacks"),
    ("Big game football Sunday", lambda y: nth_weekday(y, 2, 6, 2), "football party snacks, wings, couch-coach humor, commercials-only fans"),
    ("College basketball bracket season", fixed(3, 17), "busted brackets, hoops humor, office pool"),
    ("Summer game showcase season", lambda y: nth_weekday(y, 6, 4, 1), "game-reveal hype, gamer life, wishlist humor"),
    ("Summer comic convention season", fixed(7, 23), "cosplay life, comic fan humor, convention survival kit"),
    ("Gaming expo season", fixed(8, 20), "gamer road trip, expo badge life, retro consoles"),
    ("Fall comic convention season", fixed(10, 8), "cosplay prep, spooky-season cosplay, fan life"),
    ("Football season kickoff", lambda y: nth_weekday(y, 9, 3, 2), "tailgating, fantasy-football humor, game-day snacks"),
    ("Basketball season tip-off", fixed(10, 21), "hoops season, sneakerhead humor, buzzer beaters"),
    ("Hockey season puck drop", fixed(10, 7), "hockey life, zamboni humor, ice rink"),
    ("Baseball opening day", fixed(3, 26), "ballpark snacks, spring baseball, peanuts and cracker jacks"),
    ("Perseid meteor shower", fixed(8, 12), "shooting stars, night-sky camping, wish upon a star, celestial"),
    ("Orionid meteor shower", fixed(10, 21), "autumn night sky, stargazing, celestial"),
    ("Leonid meteor shower", fixed(11, 17), "stargazing, cozy blanket under the stars"),
    ("Geminid meteor shower", fixed(12, 13), "winter stargazing, shooting stars, cocoa under the stars"),
    ("Lyrid meteor shower", fixed(4, 22), "spring night sky, celestial"),
    ("Total solar eclipse", table({2027: (8, 2), 2028: (7, 22)}), "eclipse chasers, sun and moon, path of totality, celestial"),
    ("Friday the 13th", friday_13ths, "superstition, black cats, lucky charms, spooky-cute bad luck humor"),
    ("National Cat Day", fixed(10, 29), "cat lovers, funny cats, cat mom and cat dad"),
    ("International Cat Day", fixed(8, 8), "cat lovers, cat puns"),
    ("National Dog Day", fixed(8, 26), "dog lovers, dog mom, goofy pups"),
    ("Talk Like a Pirate Day", fixed(9, 19), "pirate puns, arr, parrots, treasure maps"),
    ("International Coffee Day", fixed(10, 1), "coffee addict humor, latte art, but first coffee"),
    ("World Kindness Day", fixed(11, 13), "be kind, positivity, cute encouragement"),
]


def _scan(events, today: dt.date, lead_days: int, source: str) -> list[TrendItem]:
    out = []
    for name, fn, hints in events:
        for year in (today.year, today.year + 1):
            ds = fn(year)
            ds = [d for d in (ds if isinstance(ds, list) else [ds]) if d is not None]
            hit = next((d for d in sorted(ds) if 0 <= (d - today).days <= lead_days), None)
            if hit is None:
                continue
            days = (hit - today).days
            h = hints.replace("{year}", str(year))
            if name == "Lunar New Year" and year in LUNAR_ANIMAL:
                h = f"year of the {LUNAR_ANIMAL[year]}, {h}"
            out.append(TrendItem(
                source=source, title=f"{name} {year}", traffic=max(100, 10_000 - days * 120),
                context=[f"in {days} days ({hit.isoformat()})", h], dedupe_period="week",
                meta={"kind": "event", "release": hit.isoformat(), "days_until": days},
            ))
            break
    return out


def upcoming(today: dt.date, lead_days: int) -> list[TrendItem]:
    return _scan(EVENTS, today, lead_days, "seasonal")


def events(today: dt.date, lead_days: int) -> list[TrendItem]:
    return _scan(POP_EVENTS, today, lead_days, "events")


def evergreen(niches: list[str], k: int) -> list[TrendItem]:
    picks = random.sample(niches, min(k, len(niches))) if niches else []
    return [TrendItem(source="evergreen", title=n, traffic=500, context=["evergreen niche, always sells"], dedupe_period="week")
            for n in picks]
