"""Repères publiés pour Forever. Coordonnées locales à chaque zone."""

from .instances import DUNGEON_CATALOG, RAID_CATALOG

ZONES = {
    "ragefire": "Orgrimmar", "thanes": "Forgefer", "lordaeron": "Clairières de Tirisfal",
    "deadmines": "Marche de l’Ouest", "wailing": "Les Tarides", "shadowfang": "Forêt des Pins-Argentés",
    "blackfathom": "Orneval", "stockade": "Hurlevent", "razorfen-kraul": "Les Tarides",
    "gnomeregan": "Dun Morogh", "wetlands": "Les Paluns", "dalaran": "Montagnes d’Alterac",
    "scarlet": "Clairières de Tirisfal", "razorfen-downs": "Les Tarides", "uldaman": "Terres ingrates",
    "maraudon": "Désolace", "zul-farrak": "Tanaris", "sunken-temple": "Marais des Chagrins",
    "blackrock-depths": "Gorge des Vents brûlants", "lower-spire": "Steppes ardentes",
    "upper-spire": "Steppes ardentes", "dire-maul": "Féralas", "stratholme": "Maleterres de l’Est",
    "scholomance": "Maleterres de l’Ouest", "barrow-deeps": "Mont Hyjal", "hyjal-summit": "Mont Hyjal",
    "onyxia": "Marécage d’Âprefange",
}
# Les fonds restent hébergés par Warcraft Forever. Chaque identifiant a été
# vérifié sur la page de la zone et correspond à son image complète.
MAP_IMAGE_ROOT = "https://wowforever-media.fly.storage.tigris.dev/maps/1.60.1.69893/zones/"
MAP_IDS = {
    "ragefire": 1454, "thanes": 1455, "lordaeron": 1420, "deadmines": 1436,
    "wailing": 1413, "shadowfang": 1421, "blackfathom": 1440, "stockade": 1453,
    "razorfen-kraul": 1413, "gnomeregan": 1426, "wetlands": 1437, "dalaran": 1416,
    "scarlet": 1420, "razorfen-downs": 1413, "uldaman": 1418, "maraudon": 1443,
    "zul-farrak": 1446, "sunken-temple": 1435, "blackrock-depths": 1427, "lower-spire": 1428,
    "upper-spire": 1428, "dire-maul": 1444, "stratholme": 1423, "scholomance": 1422,
    "barrow-deeps": 2482, "hyjal-summit": 2482, "onyxia": 1445,
}


def map_backgrounds() -> dict[str, str]:
    maps = {ZONES[key]: f"{MAP_IMAGE_ROOT}{image_id}.webp" for key, image_id in MAP_IDS.items()}
    maps["Les Pitons du Tonnerre"] = f"{MAP_IMAGE_ROOT}1456.webp"
    return maps


KINDS = {"dungeon": "Donjons", "raid": "Raids", "quest": "Quêtes", "mob": "Mobs"}


def points():
    result = []
    for item in (*DUNGEON_CATALOG, *RAID_CATALOG):
        xy = [float(value) for value in item.coordinates.split(",")] if item.coordinates else None
        if item.key == "onyxia":
            xy = [52.3, 76.1]
        result.append({"key": item.key, "name": item.name, "kind": item.kind, "zone": ZONES[item.key], "xy": xy,
                       "detail": item.access, "level": item.level, "size": item.size,
                       "composition": item.composition,
                       "guide": "https://wowfdb.com/zones/onyxia-s-lair/" if item.key == "onyxia" else item.guide})
    result.extend([
        {"key": "arugal-quest", "name": "Arugal doit mourir", "kind": "quest", "zone": "Forêt des Pins-Argentés",
         "xy": [44.3, 40.7], "detail": "Dalar Tissenuit, départ et retour de la quête.",
         "guide": "https://db.icy-veins.com/wow-forever/quests/1014"},
        {"key": "old-murk-eye", "name": "Vieux Troublœil", "kind": "mob", "zone": "Marche de l’Ouest",
         "xy": [29.3, 75.9], "detail": "Un des points de passage documentés sur la côte.",
         "guide": "https://azeroth.wiki/forever/map/westfall/old-murk-eye"},
        {"key": "defias", "name": "La Confrérie défias", "kind": "quest", "zone": "Marche de l’Ouest",
         "xy": [56.3, 47.5], "detail": "Gryan Stoutmantle, départ et retour de la quête.",
         "guide": "https://db.icy-veins.com/wow-forever/quests/166"},
        {"key": "fang", "name": "Les chefs du Croc", "kind": "quest", "zone": "Les Pitons du Tonnerre",
         "xy": [75.8, 31.2], "detail": "Nara Wildmane, donneuse de quête.",
         "guide": "https://db.icy-veins.com/wow-forever/quests/914"},
        {"key": "foe-reaper", "name": "Faucheur 4000", "kind": "mob", "zone": "Marche de l’Ouest",
         "xy": [45.1, 69.3], "detail": "Un point de passage relevé. Plusieurs positions sont documentées.",
         "guide": "https://azeroth.wiki/forever/map/westfall/foe-reaper-4000"},
    ])
    mobs = (
        ("takk", "Takk le bondisseur", "wailing", 59.6, 8.3, "Rare · niveau 19", "the-barrens/takk-the-leaper"),
        ("humar", "Humar le Fier", "wailing", 62.1, 33.3, "Rare · niveau 23", "the-barrens/humar-the-pridelord"),
        ("son-of-arugal", "Fils d’Arugal", "shadowfang", 44.8, 34.5, "Créature · niveaux 24–25", "silverpine-forest/son-of-arugal"),
        ("timber", "Timber", "gnomeregan", 34.1, 42.6, "Rare · niveau 10", "dun-morogh/timber"),
        ("vagash", "Vagash", "gnomeregan", 62.6, 46.1, "Créature · niveau 11", "dun-morogh/vagash"),
        ("mist-howler", "Hurleur des brumes", "blackfathom", 26.8, 18.4, "Rare", "ashenvale/mist-howler"),
        ("bayne", "Bayne", "lordaeron", 39.9, 42.2, "Rare", "tirisfal-glades/bayne"),
        ("coast-strider", "Coast Strider", "zul-farrak", 68.5, 59.7, "Créature", "tanaris/coast-strider"),
    )
    result.extend({"key": key, "name": name, "kind": "mob", "zone": ZONES[zone], "xy": [x, y],
                   "detail": f"{detail}. Position documentée, susceptible de varier.",
                   "guide": f"https://azeroth.wiki/forever/map/{guide}"}
                  for key, name, zone, x, y, detail, guide in mobs)
    return result
