"""Fiches de préparation pour les instances de WoW Forever."""

from dataclasses import dataclass

DUNGEON_GUIDES = {
    "ragefire": "ragefire-chasm",
    "thanes": "the-hall-of-thanes",
    "lordaeron": "ruins-of-lordaeron",
    "deadmines": "deadmines",
    "wailing": "wailing-caverns",
    "shadowfang": "shadowfang-keep",
    "blackfathom": "blackfathom-deeps",
    "stockade": "stormwind-stockade",
    "razorfen-kraul": "razorfen-kraul",
    "gnomeregan": "gnomeregan",
    "wetlands": "excavation-site-wetlands",
    "dalaran": "city-of-dalaran",
    "scarlet": "scarlet-monastery",
    "razorfen-downs": "razorfen-downs",
    "uldaman": "uldaman",
    "maraudon": "maraudon",
    "zul-farrak": "zul-farrak",
    "sunken-temple": "sunken-temple",
    "blackrock-depths": "blackrock-depths",
    "lower-spire": "blackrock-spire",
    "dire-maul": "dire-maul",
    "stratholme": "stratholme",
    "scholomance": "scholomance",
    "upper-spire": "blackrock-spire",
}
RAID_GUIDE = "https://news.blizzard.com/fr-fr/article/24303009/apercu-des-raids-de-world-of-warcraft-forever-emplacements-taille-des-raids-et-harmonisation"
DUNGEON_COMPOSITION = "1 tank, 1 soigneur, 3 DPS. Prévoyez au moins une interruption et du contrôle."
RAID_OPENING = "Ouverture annoncée le 9 décembre 2026."


@dataclass(frozen=True)
class Instance:
    key: str
    name: str
    kind: str
    level: int
    size: int
    access: str
    coordinates: str
    preparation: str
    composition: str
    guide: str
    availability: str = ""


def dungeon(key: str, name: str, level: int, access: str, coordinates: str,
            preparation: str, size: int = 5, composition: str = DUNGEON_COMPOSITION) -> Instance:
    guide = f"https://wowfdb.com/zones/{DUNGEON_GUIDES[key]}/"
    return Instance(key, name, "dungeon", level, size, access, coordinates, preparation, composition, guide)


DUNGEON_CATALOG = (
    dungeon("ragefire", "Gouffre de Ragefeu", 13, "Orgrimmar, Faille de l’Ombre", "53.0, 48.9",
            "Point de rendez-vous côté Horde. Gardez de l’espace près des bords si un ennemi repousse."),
    dungeon("thanes", "The Hall of Thanes", 13, "Forgefer, sous la Grande Forge", "",
            "Fixez un rendez-vous à la Grande Forge. L’approche est plus simple côté Alliance."),
    dungeon("lordaeron", "Ruines de Lordaeron", 15, "Clairières de Tirisfal, au-dessus de Fossoyeuse", "",
            "Fixez le point de rendez-vous avant de traverser les zones gardées côté Horde."),
    dungeon("deadmines", "Les Mortemines", 16, "Marche de l’Ouest, près de Ruisselune", "38.2, 77.5",
            "Prenez vos quêtes avant de descendre. Marquez la cible à contrôler dans les groupes de pirates."),
    dungeon("wailing", "Cavernes des lamentations", 17, "Tarides, oasis de Verte-Queue", "46.0, 36.3",
            "Interrompez les soins et les sommeils. Gardez du temps pour l’escorte finale."),
    dungeon("shadowfang", "Donjon d’Ombrecroc", 18, "Forêt des Pins-Argentés, au-dessus du village de Bois-du-Bûcher", "44.7, 67.8",
            "Désignez les interruptions. Un mage ou un druide peut aider à retirer les malédictions."),
    dungeon("blackfathom", "Profondeurs de Brassenoire", 22, "Orneval, côte nord-ouest", "16.5, 11.0",
            "Rassemblez-vous à l’entrée avant de descendre et gardez les contrôles pour les groupes les plus denses."),
    dungeon("stockade", "La Prison", 23, "Hurlevent, quartier des Mages", "50.4, 66.2",
            "L’accès se fait dans Hurlevent. Évitez de lancer plusieurs salles en même temps."),
    dungeon("razorfen-kraul", "Kraal de Tranchebauge", 24, "Tarides, à l’extrême sud", "42.3, 89.9",
            "Gardez interruptions et étourdissements pour les chamans et les groupes nombreux."),
    dungeon("gnomeregan", "Gnomeregan", 25, "Dun Morogh, à l’ouest", "17.7, 39.2",
            "Prévoyez des interruptions et surveillez les bombes dans les couloirs étroits."),
    dungeon("wetlands", "Site de fouilles des Paluns", 26, "Les Paluns, site de fouilles", "",
            "Prévoyez de quoi retirer les maladies. Identifiez les saignements à soigner en priorité."),
    dungeon("dalaran", "Cité de Dalaran", 28, "Montagnes d’Alterac, par les égouts", "",
            "Fixez le rendez-vous aux égouts et décidez avant la sortie si vous visez le boss optionnel."),
    dungeon("scarlet", "Monastère écarlate", 30, "Clairières de Tirisfal, au nord-est", "85.1, 31.4",
            "Choisissez l’aile. Vérifiez que quelqu’un a la clé écarlate pour l’Armurerie ou la Cathédrale."),
    dungeon("razorfen-downs", "Souilles de Tranchebauge", 34, "Tarides, à l’extrême sud", "50.9, 92.9",
            "Gardez de la marge en mana et des interruptions pour les lanceurs de sorts."),
    dungeon("uldaman", "Uldaman", 35, "Terres ingrates, au nord", "44.2, 12.2",
            "Récupérez les quêtes avant de partir et convenez du parcours si certains visent une étape précise."),
    dungeon("maraudon", "Maraudon", 42, "Désolace, vallée des Lances", "29.2, 62.5",
            "Choisissez la branche avant le départ. Vérifiez si le groupe dispose du raccourci."),
    dungeon("zul-farrak", "Zul’Farrak", 42, "Tanaris, au nord-ouest", "38.7, 19.9",
            "Gardez du temps pour l’événement de la pyramide et décidez si vous tentez les boss optionnels."),
    dungeon("sunken-temple", "Temple d’Atal’Hakkar", 45, "Marais des Chagrins, à l’est", "77.3, 35.9",
            "Prenez vos quêtes de classe et convenez des boss visés avant d’entrer."),
    dungeon("blackrock-depths", "Profondeurs de Rochenoire", 48, "Mont Rochenoire, accès par la Gorge des Vents brûlants", "27.1, 72.5",
            "Précisez le parcours, les quêtes et le boss final visé. Vérifiez les clés nécessaires à la route choisie."),
    dungeon("lower-spire", "Pic Rochenoire inférieur", 53, "Mont Rochenoire, accès par les Steppes ardentes", "33.0, 25.2",
            "Indiquez que vous faites la partie inférieure. Identifiez les quêtes ou étapes d’accès utiles au groupe."),
    dungeon("upper-spire", "Pic Rochenoire supérieur", 53, "Mont Rochenoire, accès par les Steppes ardentes", "33.0, 25.2",
            "Vérifiez l’accès à la partie supérieure et les quêtes liées à Onyxia avant de partir.", 10,
            "Groupe de 10. Désignez un tank principal, un second tank et répartissez les soins."),
    dungeon("dire-maul", "Hache-Tripes", 54, "Féralas, à l’est", "62.0, 33.3",
            "Choisissez l’aile et précisez si vous visez un parcours tribut au nord."),
    dungeon("stratholme", "Stratholme", 55, "Maleterres de l’Est, au nord-ouest", "26.1, 10.4",
            "Choisissez le côté écarlate ou mort-vivant et vérifiez l’accès correspondant."),
    dungeon("scholomance", "Scholomance", 57, "Maleterres de l’Ouest, Caer Darrow", "69.7, 73.4",
            "Vérifiez l’accès, prenez vos quêtes et prévoyez du contrôle pour les salles denses."),
)

RAID_CATALOG = (
    Instance("barrow-deeps", "Profondeurs des Saisons", "raid", 60, 10,
             "Accès par le mont Hyjal, le Berceau-de-l’Hiver ou le Repaire des Grumegueules", "",
             "Choisissez une entrée commune et répartissez les interruptions avant le premier pull.",
             "Commencez avec 2 tanks, 2 soigneurs et 6 DPS, puis adaptez selon les boss.", RAID_GUIDE, RAID_OPENING),
    Instance("hyjal-summit", "Sommet d’Hyjal", "raid", 60, 20,
             "Mont Hyjal, près de Nordrassil", "",
             "Choisissez votre itinéraire après le gardien et prévoyez les affectations de soins et d’interruption.",
             "Désignez un tank principal et un second tank, répartissez les soins, combinez DPS distance et mêlée.", RAID_GUIDE, RAID_OPENING),
    Instance("onyxia", "Repaire d’Onyxia", "raid", 60, 40,
             "Marécage d’Âprefange", "",
             "Chaque joueur doit avoir l’amulette Drakefeu dans son inventaire. Préparez la résistance au Feu et la phase aérienne.",
             "Prévoyez des tanks pour Onyxia et les dragonnets, des soins répartis et assez de DPS à distance.", RAID_GUIDE, RAID_OPENING),
)

INSTANCE_CATALOG = {instance.key: instance for instance in (*DUNGEON_CATALOG, *RAID_CATALOG)}
