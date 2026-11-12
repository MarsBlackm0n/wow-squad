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
KINDS = {"dungeon": "Donjons", "raid": "Raids", "quest": "Quêtes", "mob": "Mobs"}


def points():
    result = []
    for item in (*DUNGEON_CATALOG, *RAID_CATALOG):
        xy = [float(value) for value in item.coordinates.split(",")] if item.coordinates else None
        if item.key == "onyxia":
            xy = [52.3, 76.1]
        result.append({"key": item.key, "name": item.name, "kind": item.kind, "zone": ZONES[item.key], "xy": xy,
                       "detail": item.access,
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
    return result
