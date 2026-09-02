"""
Imposter — word list for the social-deduction bluffing game. Pure data.

Everyone is shown the CATEGORY. The crew also sees the secret WORD; the
imposter(s) only see "IMPOSTER" and have to bluff a fitting clue and work out
the word from everyone else's clues.

  CATEGORIES : dict[str, list[str]]
  pick_word(rng) -> (category, word)
"""

import random

CATEGORIES = {
    "Animals": [
        "Elephant", "Penguin", "Kangaroo", "Octopus", "Cheetah", "Hedgehog",
        "Dolphin", "Crocodile", "Koala", "Owl", "Chameleon", "Wolf",
        "Bat", "Sloth", "Peacock", "Hamster",
    ],
    "Fruits & Vegetables": [
        "Pineapple", "Avocado", "Broccoli", "Watermelon", "Pomegranate",
        "Cucumber", "Mango", "Eggplant", "Raspberry", "Corn", "Pumpkin",
        "Coconut", "Spinach", "Cherry", "Onion", "Grapefruit",
    ],
    "Jobs": [
        "Firefighter", "Astronaut", "Plumber", "Chef", "Lifeguard",
        "Electrician", "Dentist", "Journalist", "Pilot", "Farmer",
        "Barber", "Referee", "Librarian", "Surgeon", "Lawyer", "Beekeeper",
    ],
    "Sports": [
        "Basketball", "Tennis", "Surfing", "Bowling", "Archery", "Boxing",
        "Golf", "Volleyball", "Fencing", "Skateboarding", "Rowing",
        "Curling", "Hockey", "Gymnastics", "Cricket", "Badminton",
    ],
    "Movies": [
        "Titanic", "Frozen", "Jaws", "Shrek", "Gladiator", "Avatar",
        "The Godfather", "Jurassic Park", "The Lion King", "Toy Story",
        "Inception", "Rocky", "Home Alone", "The Matrix", "Up", "Grease",
    ],
    "Countries": [
        "Japan", "Brazil", "Egypt", "Canada", "Iceland", "Australia",
        "India", "Kenya", "Norway", "Mexico", "Greece", "Thailand",
        "Peru", "Morocco", "Ireland", "Switzerland",
    ],
    "Kitchen Items": [
        "Blender", "Toaster", "Whisk", "Colander", "Rolling pin", "Kettle",
        "Ladle", "Cutting board", "Corkscrew", "Oven mitt", "Spatula",
        "Grater", "Frying pan", "Peeler", "Measuring cup", "Tongs",
    ],
    "Around the House": [
        "Doorbell", "Staircase", "Fireplace", "Bathtub", "Ceiling fan",
        "Doormat", "Mailbox", "Thermostat", "Bookshelf", "Curtains",
        "Garage", "Attic", "Chimney", "Light switch", "Faucet", "Closet",
    ],
    "Transportation": [
        "Helicopter", "Submarine", "Bicycle", "Hot air balloon", "Tractor",
        "Ferry", "Ambulance", "Skateboard", "Rickshaw", "Canoe", "Subway",
        "Cable car", "Scooter", "Sailboat", "Monorail", "Jet ski",
    ],
    "Musical Instruments": [
        "Trumpet", "Violin", "Drums", "Accordion", "Harp", "Saxophone",
        "Flute", "Bagpipes", "Xylophone", "Banjo", "Cello", "Tambourine",
        "Harmonica", "Trombone", "Ukulele", "Piano",
    ],
    "Body Parts": [
        "Elbow", "Eyebrow", "Ankle", "Knuckle", "Shoulder", "Tongue",
        "Heel", "Wrist", "Spine", "Eyelash", "Jaw", "Thumb", "Kneecap",
        "Nostril", "Collarbone", "Earlobe",
    ],
    "Weather & Nature": [
        "Tornado", "Rainbow", "Avalanche", "Lightning", "Fog", "Glacier",
        "Volcano", "Hurricane", "Dew", "Quicksand", "Geyser", "Drought",
        "Blizzard", "Tide", "Canyon", "Waterfall",
    ],
    "Holidays & Events": [
        "Halloween", "Thanksgiving", "Wedding", "Graduation", "New Year's Eve",
        "Birthday party", "Easter", "Valentine's Day", "Carnival",
        "Baby shower", "Fourth of July", "Prom", "Retirement party",
        "Housewarming", "Reunion", "Christmas",
    ],
    "Clothing": [
        "Tuxedo", "Raincoat", "Flip-flops", "Mittens", "Bow tie", "Overalls",
        "Scarf", "Cowboy boots", "Swimsuit", "Cardigan", "Beanie", "Poncho",
        "Sneakers", "Apron", "Sunglasses", "Belt",
    ],
    "Space": [
        "Black hole", "Saturn", "Asteroid", "Rocket", "Solar eclipse",
        "Comet", "Space station", "Galaxy", "Moon landing", "Meteor shower",
        "Satellite", "Constellation", "Milky Way", "Telescope", "Mars rover",
        "Supernova",
    ],
    "Breakfast Foods": [
        "Pancakes", "Bacon", "Omelette", "Cereal", "Bagel", "Waffles",
        "Oatmeal", "Yogurt", "French toast", "Hash browns", "Croissant",
        "Grapefruit", "Smoothie", "Toast", "Muffin", "Scrambled eggs",
    ],
}


def pick_word(rng=random):
    """Return (category_name, word)."""
    cat = rng.choice(list(CATEGORIES))
    return cat, rng.choice(CATEGORIES[cat])
