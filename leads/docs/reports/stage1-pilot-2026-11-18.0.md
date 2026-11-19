# Stage 1 pilot report: US, plumbing, hvac, dental

Generated 2026-11-19 11:20 UTC. Overture release `2026-11-18.0`, config hash `65707bade42605c9`, sample seed `stage1-v1`.

## 1. Counts

| category | raw | excluded | duplicates merged | kept | in pilot sample (Postgres) |
|---|---|---|---|---|---|
| plumbing | 48,500 | 4,782 | 4,327 | 39,391 | 8,000 |
| hvac | 65,968 | 4,316 | 6,313 | 55,339 | 6,000 |
| dental | 201,802 | 20,110 | 42,624 | 139,068 | 6,000 |
| **total** | **316,270** | **29,208** | **53,264** | **233,798** | **20,000** |

## 2. Exclusions by reason

First matching reason wins, in this order: no_name, closed, institution, referral_listing, brand_wikidata, chain_name_pattern, answering_vendor, exclusion_keyword, chain_multi_location, chain_name_network.

| reason | plumbing | hvac | dental |
|---|---|---|---|
| brand_wikidata | 681 | 0 | 168 |
| chain_multi_location | 932 | 1,992 | 10,522 |
| chain_name_network | 113 | 65 | 334 |
| chain_name_pattern | 2,229 | 1,139 | 3,315 |
| closed | 554 | 1,023 | 3,726 |
| exclusion_keyword | 227 | 63 | 1,326 |
| institution | 4 | 11 | 454 |
| no_name | 3 | 2 | 20 |
| referral_listing | 39 | 21 | 245 |

## 3. Contact coverage (kept businesses)

Phone = a valid, non-toll-free, non-suspicious number (still **unverified**: verification is Stage 3). Website = the business's own site, not a directory or social page. Emails here are **source-listed** (Overture), not yet seen on the business's own site, so none of them is exportable yet.

| metric | plumbing | hvac | dental |
|---|---|---|---|
| usable phone | 97.3% | 96.9% | 97.9% |
| own website | 74.5% | 78.5% | 83.2% |
| any source-listed email | 34.0% | 31.9% | 56.8% |
| email on the site's own domain | 10.0% | 10.8% | 26.0% |
| only free-mail emails (gmail etc.) | 19.1% | 16.4% | 20.3% |
| multiple locations (2-9) | 13.2% | 14.2% | 19.5% |
| phone shared with >3 records | 0.4% | 0.4% | 3.3% |
| name repeated at 10+ locations (review) | 2.7% | 1.5% | 3.5% |
| generic name (e.g. 'Dentist') | 0.3% | 0.2% | 0.4% |
| state unknown | 0.1% | 0.1% | 0.0% |

| category | phone | phone + website (crawlable) |
|---|---|---|
| dental | 97.9% | 82.1% |
| hvac | 96.9% | 76.1% |
| plumbing | 97.3% | 72.6% |

## 4. Dedupe

Merges by matching key (union-find, so chains of matches collapse): {'phone': 44060, 'domain_zip': 8179, 'name_zip': 1025}.
 Phones listed by more than 3 records are never used to merge (call centers, tracking numbers, franchise lines): 1,390 such numbers.

## 5. Chain, platform and multi-location review

A website domain at >= 10 locations (distinct ZIPs) is excluded as a chain. *Top name* = share of its listings with the most common name; *label in name* = share whose name contains the domain's label. Real chains score high on one of the two; **low on both usually means a platform, directory or manufacturer dealer page** (add it to `normalization.non_business_site_domains`) or a DSO that keeps practice names (correctly excluded).

| domain | locations | top name | label in name |
|---|---|---|---|
| aspendental.com | 1154 | 0.49 | 0.7 |
| rotorooter.com | 959 | 0.36 | 1.0 |
| affordabledentures.com | 455 | 0.73 | 0.89 |
| searshomeservices.com | 424 | 0.86 | 0.07 |
| heartland.com | 333 | 0.02 | 0.01 |
| mrrooter.com | 290 | 0.11 | 0.99 |
| smiledirectclub.com | 279 | 0.97 | 0.99 |
| greatexpressions.com | 249 | 0.43 | 0.79 |
| westerndental.com | 241 | 0.46 | 0.87 |
| aireserv.com | 227 | 0.02 | 0.97 |
| brightnow.com | 223 | 0.7 | 0.84 |
| ferguson.com | 190 | 0.76 | 0.95 |
| interdent.com | 180 | 0.05 | 0.0 |
| comfortdental.com | 161 | 0.45 | 0.77 |
| myidealdental.com | 158 | 0.11 | 0.11 |
| midwest-dental.com | 158 | 0.3 | 0.95 |
| mysagedental.com | 155 | 0.17 | 0.17 |
| smiledoctors.com | 141 | 0.53 | 0.91 |
| dentalworks.com | 133 | 0.14 | 0.64 |
| mykoolsmiles.com | 131 | 0.67 | 0.22 |
| coastdental.com | 126 | 0.73 | 0.8 |
| benjaminfranklinplumbing.com | 123 | 0.34 | 0.93 |
| onehourheatandair.com | 116 | 0.31 | 0.02 |
| ushomeservices.us | 103 | 0.02 | 0.0 |
| monarchdental.com | 94 | 0.58 | 0.88 |
| brident.com | 91 | 0.87 | 0.93 |
| greenbergdental.com | 89 | 0.79 | 0.81 |
| mintdentistry.com | 86 | 0.43 | 0.85 |
| advantagedental.com | 81 | 0.88 | 0.94 |
| chemed.com | 81 | 0.4 | 0.0 |
| johnsoncontrols.com | 77 | 0.35 | 1.0 |
| carrierenterprise.com | 77 | 0.9 | 0.94 |
| authoritybrands.com | 75 | 0.42 | 0.0 |
| 1tomplumber.com | 73 | 0.91 | 1.0 |
| castledental.com | 71 | 0.67 | 0.96 |
| diamondbraces.com | 70 | 0.74 | 0.99 |
| zoomdrain.com | 67 | 0.84 | 0.97 |
| clearchoice.com | 67 | 0.49 | 0.66 |
| dentaldreams.com | 67 | 0.64 | 0.67 |
| neighborlybrands.com | 66 | 0.09 | 0.0 |

Detected automatically as subdomain platforms (each business has its own subdomain; keyed by full host):

| platform domain | listings |
|---|---|
| wix.com | 50 |
| hibuwebsites.com | 48 |
| webs.com | 38 |
| jobbersites.com | 34 |
| ueniweb.com | 33 |
| vpweb.com | 32 |
| jany.io | 19 |
| edan.io | 19 |
| hibustudio.com | 19 |
| homestead.com | 19 |
| yolasite.com | 17 |
| ruudpropartner.com | 16 |
| rheempropartner.com | 15 |
| ypeek.com | 13 |
| rheemdealer.net | 12 |

Multi-location businesses kept (2-9 locations; value signal, check decision-maker). Top:

| domain | locations |
|---|---|
| sunshine-plumbers.com | 9 |
| hometowndental.org | 9 |
| appleortho.com | 9 |
| medicomdtx.com | 9 |
| assuredentalcare.com | 9 |
| mikediamondservices.com | 9 |
| orangecoastplumbing.net | 9 |
| bluediamondwaterheaterrepairco.com | 9 |
| desertvalleydental.com | 9 |
| somosdental.com | 9 |
| kidskarepc.com | 9 |
| cooltechhvacservices.com | 9 |
| texasgentledental.com | 9 |
| bayoubraces.com | 9 |
| drtrevisani.com | 9 |

Names at >= 10 locations. *excluded* = at least 50% of the listings share one website (chain or listing network); *review* = different websites (usually a generic name; add real chains to `chain_name_patterns`):

| category | name | locations | top domain | result |
|---|---|---|---|---|
| dental | family dental care | 81 | zamoradental.com | review |
| dental | gentle dental | 59 | gentledentalmaine.com | review |
| dental | endodontic associates | 59 | slidellendo.com | review |
| dental | dentistry for children | 57 | dentistryforchildrenky.com | review |
| dental | main street dental | 52 | mainstreetdental.us | review |
| dental | gentle dental care | 50 | gentledentalnampa.com | review |
| dental | family and cosmetic dentistry | 50 | alleghenysmiles.com | review |
| plumbing | mr drain plumbing | 46 | mrdrain.pro | review |
| dental | smile center | 45 | thesmilecenterusa.com | review |
| dental | gentle family dentistry | 44 | gfdmaine.com | review |
| dental | dental depot | 41 | dentaldepot.net | review |
| dental | oral and maxillofacial surgery | 37 | kytnoralsurgery.com | review |
| plumbing | mr rescue plumbing and drain cleaning | 37 | mrplumbingdraincleaning.com | excluded |
| dental | advanced dental care | 36 | advanceddentalcare.net | review |
| hvac | trane | 35 | nwhawaiitrane.com | review |
| dental | complete dental care | 35 | completedentalcaresmile.com | review |
| dental | smile design dentistry | 34 | thesmiledesignfourthstreet.com | review |
| dental | premier dental | 34 | premiersmile.com | review |
| dental | art of dentistry | 33 | artofdentistryny.com | review |
| dental | downtown dental | 33 | downtowndentalsc.com | review |
| dental | oral and maxillofacial surgery associates | 33 | jawdocs.net | review |
| dental | dentistry by design | 32 | dbdfl.com | review |
| dental | hometown dental | 32 | hometowndental.org | review |
| dental | signature smiles | 31 | signsmiles.com | review |
| dental | advanced endodontics | 31 | ineedarootcanal.com | review |
| dental | apex endodontics | 31 | apex-endodontics.com | review |
| dental | orthodontic specialists | 30 | nodownpayment4braces.com | review |
| dental | smiles by design | 30 | smilesbydesignva.com | review |
| dental | cornerstone dental | 30 | cornerstonedentalny.com | review |
| dental | endodontic specialists | 30 | arootcanalspecialist.com | review |

## 6. Top states (kept)

| state | businesses |
|---|---|
| CA | 31,590 |
| TX | 22,189 |
| FL | 17,760 |
| NY | 13,162 |
| IL | 9,588 |
| PA | 8,325 |
| NJ | 7,213 |
| OH | 7,118 |
| NC | 6,694 |
| GA | 6,665 |
| MI | 6,599 |
| VA | 6,044 |

## 7. Twenty sample rows (from Postgres, pilot sample)

Email addresses are not printed here (some are personal); the count is shown.

| name | category | city | tz | phone | domain | locations | emails | flags |
|---|---|---|---|---|---|---|---|---|
| TDAC Heating & Air Conditioning LLC | hvac/hvac_service | Mansfield, TX | America/Chicago | +16822767702 | tdacheatair.com | 1 | 0 |  |
| Best Price Heating, Cooling, Plumbing | plumbing/plumbing | Seaford, DE | America/New_York | +13026293753 | - | 1 | 1 | no_website |
| Baikal Mechanical | hvac/hvac_service | Brooklyn, NY | America/New_York | +17184997200 | baikalmechanical.com | 2 | 1 | multiple_locations |
| Glenbrook Oral & Maxillofacial Surgery | dental/general_dentistry | Glenview, IL | America/Chicago | +18479988959 | omfsurg.com | 1 | 1 |  |
| Wallace F. Chong, DDS | dental/general_dentistry | Hilo, HI | Pacific/Honolulu | +18089355651 | wallacechongdds.com | 1 | 0 |  |
| Ross and Sweet Dentistry | dental/general_dentistry | Temple, TX | America/Chicago | +12547787581 | dentistintemple.com | 1 | 0 |  |
| Liberty Dental Inc | dental/general_dentistry | Havana, IL | America/Chicago | +13095436301 | libertydental.co | 2 | 0 | multiple_locations name_repeated_review |
| Leakesville Dental Clinic | dental/general_dentistry | Leakesville, MS | America/Chicago | +16013942640 | coastalfamilyhealth.com | 1 | 0 |  |
| Baymeadows Family Dentistry | dental/general_dentistry | Jacksonville, FL | America/New_York | +19047393939 | baymeadowsfamilydentistry.com | 1 | 1 |  |
| Kurt's Plumbing & Heating | hvac/hvac_service | Monona, IA | America/Chicago | +15635392605 | - | 1 | 0 | not_own_site:lennox.com no_website |
| Advanced Cooling Systems | hvac/hvac_service | Clearwater, FL | America/New_York | +17273347155 | acs-advancedcooling.com | 1 | 0 |  |
| North Point Heating & Cooling LLC | hvac/hvac_service | Eileen, WI | America/Chicago | +17154132402 | - | 1 | 1 | no_website |
| Best Value Plumbing & Rooter | plumbing/plumbing | Great Falls, MT | America/Denver | +14064539626 | bestvalueplumbingandrooter.com | 1 | 1 |  |
| Walker & Son's Plumbing | plumbing/plumbing | Fort Washington, MD | America/New_York | +13018937603 | walkerandsonsplumbing.com | 1 | 0 |  |
| Dental Bar | dental/general_dentistry | Dearborn, MI | America/Detroit | +13135006332 | dentalbarmi.com | 1 | 0 |  |
| Scotty Plumbing | plumbing/plumbing | Humble, TX | America/Chicago | +12815487733 | scottyplumbing.com | 1 | 0 |  |
| Start Smiling Dental Implant Centers | dental/specialists | Oakbrook Terrace, IL | America/Chicago | +16304734550 | startsmilingdentalimplantcenters.com | 1 | 0 |  |
| The Plumbing Connection | plumbing/plumbing | Mesquite, TX | America/Chicago | +19722857121 | theplumbingconnection.biz | 3 | 0 | multiple_locations |
| Pipe Fitters Plumbing, Heating And Cooling Llc | plumbing/plumbing | Kenilworth, NJ | America/New_York | +19082412362 | pipefittersplumbing.com | 1 | 0 |  |
| SOTAC Plumbing Services | plumbing/plumbing | Fenton, MO | America/Chicago | +13144679687 | thesotacco.com | 1 | 1 |  |

## 8. Projection to the full universe

| measure | pilot | at 3,000,000 businesses |
|---|---|---|
| raw extract Parquet | 31.8 MB (101 B/row) | 0.30 GB |
| built Parquet | 35.4 MB (112 B/row) | 0.34 GB |
| Postgres (businesses+phones+emails, with indexes) | 2674 B/business | 8.02 GB |
| extract time (remote read) | 51 s | scales with files read, not rows: all US categories ~ same |
| build time | 54 s for 316,270 rows | ~9 min |
| cost | $0 | $0 (open data; LLM/paid steps only on the top slices, see `leads config validate`) |

US places in all 7 configured categories (primary code, before exclusions/dedupe): **930,691** (auto_repair 320,919, dental 201,802, electrical 43,015, hvac 65,968, law_small 177,894, plumbing 48,500, roofing_restoration 72,593).
 Applying this pilot's kept rate gives the US share of the 3M target; CA/AU/UK fill the rest in Stage 7.

Load: {'pilot_rows_in_parquet': 20000}.
