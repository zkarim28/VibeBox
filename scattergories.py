"""
Scattergories game logic — data + pure helpers, no server/IO concerns.

Rules (as configured for this build):
  * One random letter per round, 12 random categories, fixed timer.
  * Answers lock in when the timer hits zero (host may end the round early).
  * Review goes category by category. For each answer, everyone EXCEPT the
    author votes yes/no. It scores if yes > no (a tie does not score). With no
    other players, an answer auto-counts.
  * If two or more players give the same answer (case-insensitive) for a
    category, every one of those answers is eliminated and scores nothing.
  * Optional: an alliteration (2+ significant words all starting with the
    letter) scores 2 instead of 1.
"""

import random
import re

# Classic Scattergories drops the hardest letters.
LETTERS = "ABCDEFGHIJKLMNOPRSTW"

CATEGORY_POOL = [
    # -- names & people --
    "Boy's Name", "Girl's Name", "Famous People", "Cartoon Characters",
    "Superheroes", "Villains", "Fictional Characters", "Historical Figures",
    "Athletes", "Musicians or Bands", "Authors", "Movie Stars", "Celebrities",
    "Nicknames", "Names for a Pet", "Names for a Boat", "Names for a Band",

    # -- places --
    "Cities", "Countries", "Places in Europe", "Bodies of Water", "Islands",
    "Mountains", "Landmarks", "National Parks", "Vacation Spots",
    "Places You'd Take a Date", "Places With Lots of People",
    "Places That Are Quiet", "Places You Wait in Line", "Places to Study",
    "Streets or Roads", "Cities You'd Like to Visit", "Tourist Traps",

    # -- animals & nature --
    "Animals", "Insects or Bugs", "Birds", "Fish", "Ocean Creatures",
    "Zoo Animals", "Farm Animals", "Pets", "Dog Breeds",
    "Animals You Wouldn't Want as a Pet", "Trees", "Flowers", "Plants",
    "Things in a Forest", "Things in the Desert", "Natural Disasters",
    "Weather Words", "Things in the Sky", "Things in the Ocean",
    "Things in Space", "Things You'd See in a Cave",

    # -- food & drink --
    "Food", "Fruits", "Vegetables", "Junk Food", "Kinds of Candy",
    "Ice Cream Flavors", "Pizza Toppings", "Breakfast Foods", "Desserts",
    "Snacks", "Sandwich Fillings", "Spices or Herbs", "Kinds of Cheese",
    "Kinds of Bread", "Sauces or Condiments", "Types of Drinks", "Cocktails",
    "Things You Order at a Bar", "Things You Eat With a Spoon",
    "Things You Eat With Your Hands", "Things You Grill", "Fast Food Items",
    "Things in the Fridge", "Things in the Pantry", "Foods That Are Green",
    "Cereal Brands", "Words to Describe Food", "Foods You Eat Cold",

    # -- household & objects --
    "Things in the Kitchen", "Things in a Bathroom", "Things in a Garage",
    "Things in a Junk Drawer", "Things in an Office", "Things in a Bag or Purse",
    "Things in a Suitcase", "Things in a Backpack", "Things on a Desk",
    "Things in a First Aid Kit", "Things in a Toolbox", "Cleaning Supplies",
    "Tools", "Kitchen Appliances", "Things That Have Buttons",
    "Things You Plug In", "Things That Have a Screen", "Things You Charge",
    "Things That Open and Close", "Things With Wheels", "Things Made of Metal",
    "Things Made of Wood", "Things Made of Glass", "Things That Are Round",
    "Things That Are Square", "Things That Are Sticky", "Things That Are Soft",
    "Things That Are Sharp", "Things That Are Fragile", "Things That Bounce",
    "Things That Float", "Things That Glow", "Things That Melt",
    "Things That Stretch", "Things That You Fold", "Things You Recycle",
    "Things You Throw Away", "Things That Are Loud", "Things That Ring",

    # -- clothing --
    "Things You Wear", "Things You Wear on Your Feet",
    "Things You Wear on Your Head", "Accessories", "Jewelry",
    "Clothing Brands", "Things in a Closet", "Winter Clothing", "Costumes",

    # -- entertainment & media --
    "Movie Titles", "Song Titles", "TV Shows", "Book Titles", "Board Games",
    "Video Games", "Card Games", "Cartoons", "Movie Genres", "Music Genres",
    "Musical Instruments", "Sitcoms", "Reality Shows", "Disney Movies",
    "Playground Games", "Party Games", "Things at a Concert", "Magazines",

    # -- activities --
    "Sports", "Hobbies", "Things You Do Every Day", "Household Chores",
    "Things You Do at a Party", "Things You Do on Vacation",
    "Things You Do to Relax", "Outdoor Activities", "Winter Activities",
    "Exercises", "Dance Moves", "Olympic Events",
    "Words Associated With Exercise", "Ways to Travel",
    "Modes of Transportation",

    # -- describe / abstract --
    "Colors", "Emotions or Feelings", "Words to Describe a Person",
    "Compliments", "Ways to Describe Someone Annoying", "School Subjects",
    "College Majors", "Languages", "Units of Measurement", "Body Parts",

    # -- situational & funny --
    "Reasons to Be Late", "Reasons to Call in Sick", "Reasons to Quit Your Job",
    "Excuses for Skipping the Gym", "Excuses for Not Doing Homework",
    "Things That Make You Angry", "Things That Make You Smile",
    "Things That Scare You", "Things That Are Overrated",
    "Things That Are Free", "Things That Are Expensive",
    "Things That Are a Waste of Money", "Things That Are Cold",
    "Things That Are Hot", "Things That Smell Bad", "Things That Smell Good",
    "Bad Habits", "New Year's Resolutions", "Things on a Bucket List",
    "Things People Collect", "Things You Lose", "Things You Forget",
    "Things You Procrastinate On", "Things You Save Up For",
    "Things You Can't Live Without", "Things You'd Save in a Fire",
    "Things That Ruin a Picnic", "Things That Come in Pairs",
    "Things You Shouldn't Say at a Job Interview",

    # -- events & seasons --
    "Things at a Wedding", "Things at a Birthday Party",
    "Things at a Carnival", "Things at the Beach", "Things in a Park",
    "Things You Bring Camping", "Things at the Airport", "Holidays",
    "Things Associated With Summer", "Things Associated With Winter",
    "Things Associated With Halloween",

    # -- work, school, health --
    "Occupations", "Jobs That Require a Uniform", "Dangerous Jobs",
    "Things on a Résumé", "Things in a Hospital", "Things at the Dentist",
    "Reasons to See a Doctor", "Things in a Medicine Cabinet",

    # -- money & shopping --
    "Brands", "Stores", "Restaurants", "Fast Food Chains",
    "Things You Buy at a Pharmacy", "Things You Buy at a Hardware Store",

    # -- transportation --
    "Cars", "Things With an Engine", "Things at a Gas Station", "Parts of a Car",

    # -- misc classic --
    "Things That Are Green", "Things That Are Yellow",
    "Things That Are Black and White", "Things That Grow",
    "Things in a Garden", "Things You Find on the Ground",
    "Things That Have Seeds", "Things That Have Stripes", "Things With a Tail",
    "Things That Fly", "Things You Whisper", "Things You Shout",
    "Things That Come Out at Night", "Things That Buzz",
]

_STOPWORDS = {"a", "an", "the", "of", "and", "or", "to", "in", "on", "at", "for"}
_NUM_CATEGORIES = 12


def new_round(rng=random):
    """Pick a fresh letter and 12 distinct categories."""
    return {
        "letter": rng.choice(LETTERS),
        "categories": rng.sample(CATEGORY_POOL, _NUM_CATEGORIES),
    }


def normalize(text):
    """Loose comparison key for duplicate detection."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def starts_with(text, letter):
    """Does the (trimmed) answer begin with the round's letter?"""
    t = (text or "").strip().lower()
    return bool(t) and t[0] == letter.lower()


def is_alliteration(text, letter):
    words = [w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
             if w not in _STOPWORDS]
    return len(words) >= 2 and all(w[0] == letter.lower() for w in words)


def score_answer(approved, text, letter, alliteration_bonus):
    """Points for a single approved answer."""
    if not approved:
        return 0
    if alliteration_bonus and is_alliteration(text, letter):
        return 2
    return 1
