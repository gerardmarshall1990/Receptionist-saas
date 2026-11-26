# Tier 1 niche universe (US), Overture 2026-11-18.0

Generated 2026-11-26 15:06 UTC by `leads universe niches`, from `built_US_chiropractic-garage_door-law_small-locksmith-pest_control-septic-towing-veterinary_76a4e4a8faecf00d.parquet`.
No crawl, no paid API: open data only. *Kept* = not excluded (closed, chain/franchise, brand,
institution, keyword, lead-gen listing) and not a duplicate. *Pilot* = rows the additive load
would take per niche (up to 5,000, stable random order, minus anything already in the database).

## Codes matched and counts per niche

| Niche | Overture codes (census, primary) | Raw | Kept | Kept with website | Kept with usable phone | Chains excluded | Duplicates | Pilot |
|---|---|---|---|---|---|---|---|---|
| Locksmiths | `key_and_locksmith` 34,969 | 34,969 | 13,580 | 9,735 (71.7%) | 13,309 (98.0%) | 19,958 | 1,215 | 5,000 |
| Towing and roadside assistance | `towing_service` 26,451 | 26,451 | 22,909 | 12,936 (56.5%) | 22,407 (97.8%) | 761 | 2,257 | 5,000 |
| Garage door repair | `garage_door_service` 17,450 | 17,450 | 9,684 | 8,001 (82.6%) | 9,078 (93.7%) | 6,720 | 895 | 5,000 |
| Pest control | `pest_control_service` 23,984 | 23,984 | 16,369 | 13,359 (81.6%) | 15,499 (94.7%) | 5,480 | 1,822 | 5,000 |
| Septic service | `septic_service` 5,693 | 5,693 | 5,152 | 3,695 (71.7%) | 5,049 (98.0%) | 45 | 459 | 5,000 |
| Chiropractic clinics | `chiropractic` 82,115 | 82,115 | 60,367 | 48,436 (80.2%) | 59,552 (98.6%) | 4,035 | 15,736 | 5,000 |
| Veterinary clinics | `veterinarian` 48,707, `veterinary_care` 265, `animal_hospital` 1,245 | 50,217 | 34,480 | 29,134 (84.5%) | 33,371 (96.8%) | 9,869 | 5,073 | 5,000 |
| Small law firms (PI, family, criminal, immigration, estate, bankruptcy): Immigration | `immigration_law` 7,634 | 7,634 | 6,392 | 5,159 (80.7%) | 6,079 (95.1%) | 154 | 996 | 5,000 |
| Small law firms (PI, family, criminal, immigration, estate, bankruptcy): Estate/wills/probate | `wills_trusts_and_probate` 5,283, `estate_planning_law` 3,116 | 8,399 | 7,085 | 6,007 (84.8%) | 6,876 (97.1%) | 158 | 1,125 | 5,000 |
| Small law firms (PI, family, criminal, immigration, estate, bankruptcy): Bankruptcy | `bankruptcy_law` 9,487 | 9,487 | 7,220 | 5,809 (80.5%) | 6,868 (95.1%) | 622 | 1,542 | 5,000 |

Already covered by existing categories (no new rows): orthodontics (dental), water heater
installation (plumbing), drain cleaning (no Overture code; sits under plumbing).

## Why so many chain exclusions (checked by hand)

- **Locksmith, 19,773 `chain_multi_location`:** KeyMe kiosks (`key.me`, 10,181) and Minute Key kiosks
  (`minutekey.com`, 7,993) inside retail stores. The rest are lead-gen networks (`locksmithprosusa.com` 696,
  `ushomeservices.us`), Yale KeyExpress, Pop-A-Lock and interlock vendors. None are small independents.
- **Garage door, 6,129:** lead-gen pages (`homedepotdoorsinstallation.com` 2,600, `homedepot.com` 453), plus
  networks (`journeygaragedoor.com` 1,506, `americansgaragedoors.com` 570, `a1garage.com`).
- **Veterinary, 9,323 chain rows:** Banfield, VCA, Petco/PetSmart clinics and multi-site corporate groups.
- **Pest control, 5,107:** Orkin, Terminix, Rentokil, Aptive and the like.

The rule is the same as for the existing niches: the same website at 10 or more locations, or a brand name
at the start of the business name.

## Kept rows per state

| State | locksmith | towing | garage_door | pest_control | septic | chiropractic | veterinary | immigration | estate_planning | bankruptcy | All |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AK | 21 | 51 | 14 | 13 | 25 | 180 | 68 | 7 | 7 | 1 | 387 |
| AL | 123 | 396 | 84 | 282 | 119 | 604 | 599 | 31 | 79 | 126 | 2,443 |
| AR | 72 | 295 | 76 | 166 | 44 | 486 | 390 | 18 | 61 | 73 | 1,681 |
| AZ | 652 | 445 | 273 | 586 | 72 | 1,354 | 723 | 107 | 205 | 220 | 4,637 |
| CA | 1,646 | 2,194 | 1,329 | 1,918 | 214 | 7,050 | 3,080 | 1,219 | 1,181 | 1,005 | 20,836 |
| CO | 414 | 414 | 337 | 162 | 73 | 1,621 | 888 | 107 | 152 | 155 | 4,323 |
| CT | 123 | 153 | 108 | 150 | 100 | 585 | 362 | 63 | 76 | 68 | 1,788 |
| DC | 32 | 30 | 3 | 10 | 0 | 51 | 21 | 88 | 21 | 26 | 282 |
| DE | 20 | 79 | 22 | 58 | 16 | 195 | 75 | 1 | 13 | 18 | 497 |
| FL | 1,278 | 1,676 | 760 | 1,928 | 400 | 4,531 | 2,604 | 963 | 734 | 661 | 15,535 |
| GA | 657 | 888 | 316 | 622 | 256 | 2,086 | 1,101 | 187 | 190 | 280 | 6,583 |
| HI | 28 | 89 | 15 | 63 | 17 | 239 | 103 | 20 | 39 | 20 | 633 |
| IA | 64 | 201 | 85 | 136 | 55 | 1,216 | 540 | 9 | 31 | 37 | 2,374 |
| ID | 54 | 174 | 48 | 136 | 47 | 477 | 241 | 25 | 38 | 31 | 1,271 |
| IL | 389 | 849 | 453 | 399 | 130 | 2,582 | 1,179 | 219 | 269 | 342 | 6,811 |
| IN | 294 | 532 | 224 | 295 | 105 | 923 | 708 | 35 | 100 | 145 | 3,361 |
| KS | 73 | 177 | 58 | 194 | 33 | 896 | 492 | 25 | 50 | 54 | 2,052 |
| KY | 98 | 390 | 83 | 165 | 80 | 675 | 578 | 31 | 70 | 93 | 2,263 |
| LA | 96 | 381 | 59 | 250 | 67 | 539 | 547 | 42 | 73 | 91 | 2,145 |
| MA | 287 | 361 | 241 | 299 | 144 | 1,024 | 566 | 164 | 218 | 156 | 3,460 |
| MD | 326 | 473 | 138 | 230 | 84 | 635 | 546 | 132 | 106 | 139 | 2,809 |
| ME | 29 | 119 | 31 | 49 | 46 | 252 | 216 | 3 | 26 | 10 | 781 |
| MI | 294 | 697 | 341 | 327 | 176 | 1,973 | 1,022 | 105 | 228 | 292 | 5,455 |
| MN | 176 | 362 | 177 | 127 | 113 | 1,828 | 636 | 60 | 99 | 123 | 3,701 |
| MO | 167 | 525 | 167 | 329 | 86 | 1,412 | 806 | 56 | 152 | 141 | 3,841 |
| MS | 54 | 259 | 45 | 131 | 51 | 255 | 335 | 16 | 37 | 61 | 1,244 |
| MT | 29 | 116 | 40 | 46 | 34 | 312 | 237 | 5 | 19 | 28 | 866 |
| NC | 285 | 906 | 226 | 461 | 284 | 1,485 | 1,161 | 146 | 193 | 126 | 5,273 |
| ND | 16 | 48 | 21 | 18 | 18 | 281 | 96 | 7 | 6 | 7 | 518 |
| NE | 49 | 119 | 54 | 75 | 24 | 577 | 311 | 20 | 29 | 37 | 1,295 |
| NH | 31 | 111 | 45 | 62 | 64 | 272 | 199 | 7 | 34 | 31 | 856 |
| NJ | 337 | 429 | 176 | 454 | 97 | 1,796 | 704 | 172 | 156 | 221 | 4,542 |
| NM | 87 | 155 | 32 | 89 | 42 | 293 | 204 | 20 | 25 | 23 | 970 |
| NV | 151 | 160 | 85 | 184 | 16 | 492 | 224 | 76 | 72 | 85 | 1,545 |
| NY | 891 | 941 | 275 | 819 | 201 | 2,803 | 1,461 | 606 | 392 | 300 | 8,689 |
| OH | 425 | 801 | 332 | 489 | 167 | 1,762 | 1,229 | 100 | 233 | 317 | 5,855 |
| OK | 90 | 336 | 129 | 319 | 97 | 725 | 604 | 34 | 90 | 91 | 2,515 |
| OR | 132 | 292 | 119 | 170 | 66 | 1,064 | 561 | 54 | 96 | 98 | 2,652 |
| PA | 358 | 848 | 298 | 420 | 176 | 2,638 | 1,274 | 129 | 264 | 226 | 6,631 |
| RI | 90 | 65 | 14 | 47 | 23 | 161 | 92 | 16 | 23 | 16 | 547 |
| SC | 152 | 557 | 80 | 366 | 121 | 1,001 | 570 | 51 | 92 | 69 | 3,059 |
| SD | 14 | 56 | 26 | 36 | 13 | 303 | 163 | 3 | 13 | 13 | 640 |
| TN | 218 | 649 | 129 | 419 | 110 | 992 | 822 | 63 | 116 | 143 | 3,661 |
| TX | 1,805 | 1,970 | 1,320 | 1,646 | 497 | 4,332 | 2,915 | 738 | 465 | 420 | 16,108 |
| UT | 96 | 235 | 162 | 208 | 13 | 700 | 257 | 44 | 87 | 96 | 1,898 |
| VA | 228 | 682 | 142 | 419 | 132 | 1,052 | 955 | 175 | 154 | 158 | 4,097 |
| VT | 14 | 62 | 13 | 17 | 19 | 123 | 116 | 2 | 7 | 4 | 377 |
| WA | 418 | 491 | 265 | 261 | 152 | 1,735 | 818 | 138 | 158 | 153 | 4,589 |
| WI | 136 | 395 | 170 | 217 | 162 | 1,476 | 736 | 47 | 86 | 151 | 3,576 |
| WV | 33 | 159 | 16 | 52 | 34 | 186 | 184 | 1 | 10 | 18 | 693 |
| WY | 24 | 87 | 23 | 28 | 21 | 125 | 114 | 4 | 8 | 21 | 455 |
| **Total** | **13,576** | **22,880** | **9,679** | **16,347** | **5,136** | **60,355** | **34,433** | **6,391** | **7,083** | **7,220** | **183,100** |
