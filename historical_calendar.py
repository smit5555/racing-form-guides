"""Recorded Racing.com Table-view meetings, September 2026.

Source: https://www.racing.com/calendar, All States, Table view, September 2026.
Read through the public browser UI on 19 October 2026. Race meetings only;
trials, jump-outs and picnic meetings excluded. Country is NOT proof of TAB.
QLD entries are reconciled with the user's Racing Queensland results calendar:
only explicitly TAB-labelled race meetings are retained there.
Venue strings below are source display names, not independently verified RA keys.
The downloader must validate each page and report unresolved/unavailable guides.
This snapshot does not establish coverage for other dates or future changes.
"""
import datetime as dt

SOURCE = 'https://www.racing.com/calendar'
STATES = ('QLD', 'NSW', 'VIC', 'SA', 'WA')
START = dt.date(2026, 9, 1)
END = dt.date(2026, 9, 18)

# Every date explicitly covers all five states. An omitted state in a day's
# compact record means no race meeting was shown, not a failed lookup.
_DAYS = {
  1: {'VIC':['Picklebet Park Wodonga'], 'NSW':['Scone']},
  2: {'VIC':['Sportsbet Sandown Hillside'], 'NSW':['Warwick Farm'], 'QLD':['Ipswich'], 'WA':['Belmont'], 'SA':['Thomas Farms RC Murray Bridge']},
  3: {'VIC':['Warrnambool'], 'NSW':['Coffs Harbour'], 'QLD':['Mackay'], 'WA':['Northam']},
  4: {'NSW':['Wyong'], 'VIC':['Mornington'], 'QLD':['Birdsville']},
  5: {'VIC':['Sportsbet Sandown Hillside','bet365 Mildura'], 'NSW':['Royal Randwick','Hawkesbury','Kempsey','Mendooran'], 'QLD':['Eagle Farm','Aquis Park Gold Coast','Birdsville','Toowoomba'], 'SA':['Morphettville Parks'], 'WA':['Belmont','Junction']},
  6: {'VIC':['Bendigo'], 'NSW':['Gundagai','Moree'], 'QLD':['Sunshine Coast'], 'WA':['Kalgoorlie']},
  7: {'VIC':['Tatura'], 'NSW':['Dubbo','Grafton']},
  8: {'VIC':['Moe'], 'NSW':['Muswellbrook','Queanbeyan'], 'QLD':['Townsville']},
  9: {'VIC':['Sportsbet Sandown Hillside'], 'NSW':['Kensington'], 'QLD':['Ipswich'], 'WA':['Belmont'], 'SA':['Thomas Farms RC Murray Bridge']},
  10: {'VIC':['bet365 Seymour'], 'NSW':['Hawkesbury','Lismore'], 'QLD':['Picklebet Park Warwick'], 'WA':['Bunbury']},
  11: {'VIC':['Ladbrokes Geelong'], 'NSW':['Goulburn','Tuncurry'], 'QLD':['Sunshine Coast'], 'SA':['Bordertown']},
  12: {'VIC':['Flemington','Donald'], 'NSW':['Rosehill Gardens','Collarenebri','Griffith','Kembla Grange'], 'QLD':['Doomben','Aquis Park Gold Coast','Mackay','Thangool','Toowoomba'], 'WA':['Belmont','Carnarvon'], 'SA':['Morphettville']},
  13: {'VIC':['bet365 Park Kilmore','Sale'], 'NSW':['Bathurst','Coffs Harbour'], 'QLD':['Dalby'], 'WA':['Northam']},
  14: {'VIC':['bet365 Hamilton'], 'NSW':['Corowa','Tamworth']},
  15: {'VIC':['Picklebet Park Wodonga'], 'NSW':['Moruya','Wellington'], 'QLD':['Sportsbet Bowen']},
  16: {'VIC':['Caulfield Heath'], 'NSW':['Canterbury Park'], 'QLD':['Doomben'], 'WA':['Belmont'], 'SA':['Balaklava']},
  17: {'VIC':['Mornington'], 'NSW':['Illawarra Grange'], 'QLD':['Aquis Beaudesert'], 'WA':['Bunbury']},
  18: {'NSW':['Newcastle'], 'VIC':['Sportsbet-Ballarat'], 'QLD':['Townsville']},
}

def lookup(date, states):
    """None means outside coverage; empty lists mean recorded no meetings."""
    if not START <= date <= END:
        return None
    if any(state not in STATES for state in states):
        raise ValueError('Historical snapshot covers QLD, NSW, VIC, SA and WA only.')
    return {state: list(_DAYS[date.day].get(state, [])) for state in states}
