# Stage 2 pilot report: crawl + signals

Generated 2026-11-19 17:05 UTC by `leads crawl report`. Config hash `b017a5253eb0905c`, user agent `FrontdeskLeadsBot/0.1`.

## 1. Crawl outcome (per website)

| status | sites | share |
|---|---|---|
| done | 9,848 | 64.0% |
| error | 2,540 | 16.5% |
| blocked | 1,483 | 9.6% |
| robots_disallowed | 778 | 5.1% |
| offsite | 724 | 4.7% |
| parked | 26 | 0.2% |

Top reasons for not-done sites:

| status | detail | sites |
|---|---|---|
| error | robots.txt unreachable: ConnectError | 1535 |
| blocked | HTTP 403 on home | 994 |
| robots_disallowed | robots.txt 403: treated as disallow-all | 665 |
| error | robots.txt unreachable: ProxyError | 407 |
| error | HTTP 404 | 300 |
| blocked | HTTP 202 on home | 233 |
| error | robots.txt unreachable: ReadTimeout | 117 |
| robots_disallowed | homepage disallowed by robots.txt | 111 |
| blocked | HTTP 403 on contact | 70 |
| blocked | HTTP 466 on home | 45 |
| error | robots.txt 503 | 26 |
| blocked | HTTP 403 on about | 26 |
| blocked | HTTP 409 on home | 23 |
| error | robots.txt 500 | 23 |
| error | HTTP 500 | 22 |

## 2. Politeness (measured from stored fetch times)

| check | rule | measured |
|---|---|---|
| idle gap: response finished -> next request to the same site | >= 2.0 s | not measurable for these pages (see next row) |
| pages fetched before request times were recorded | paced start-to-start in code: >= delay between request starts | 33,443 pages |
| max pages fetched from one site | <= 5 | 5 |
| robots.txt | fetched first; 401/403 = disallow all | enforced in code + tests |
| blocks (403/429/challenge) | stop, never retry | 1483 sites |

Pages by kind: home 10,754, contact 7,292, about 6,355, services 5,347, booking 2,048, careers 1,647.

## 3. Coverage per category (crawled businesses)

| category | crawled | email on own site | phone confirmed by site | site mismatch | listed phone not on site | answering service (DQ) | franchise (DQ) | blocked | thin homepage (JS?) |
|---|---|---|---|---|---|---|---|---|---|
| dental | 6,933 | 27.1% | 45.9% | 6.5% | 17.1% | 0.6% | 0.1% | 13.7% | 3.2% |
| hvac | 5,729 | 32.4% | 37.8% | 7.8% | 19.8% | 0.7% | 0.1% | 12.1% | 4.3% |
| plumbing | 6,995 | 33.1% | 37.5% | 7.9% | 21.0% | 0.8% | 0.1% | 9.1% | 4.6% |

## 4. Signal coverage (share of businesses whose site loaded)

| signal | dental | hvac | plumbing |
|---|---|---|---|
| phone_only_booking | 3.0% | 1.6% | 1.7% |
| no_online_booking | 41.2% | 51.5% | 56.7% |
| no_chat_widget | 81.8% | 80.8% | 83.4% |
| single_phone_number | 44.2% | 53.1% | 52.8% |
| leave_message_text | 0.5% | 0.3% | 0.4% |
| limited_hours_text | 32.3% | 13.4% | 13.4% |
| emergency_247_claim | 33.7% | 52.7% | 54.9% |
| hiring_receptionist | 0.9% | 1.1% | 0.8% |
| pricing_hints | 8.5% | 14.3% | 13.0% |
| multiple_locations | 20.9% | 6.5% | 4.4% |
| website_works | 100.0% | 100.0% | 100.0% |
| owner_operated | 5.5% | 40.8% | 40.2% |
| uses_answering_service | 0.8% | 1.0% | 1.1% |
| is_chain | 0.2% | 0.2% | 0.1% |

Businesses with at least one evidence-backed **pain** reason: dental 97.4%, hvac 98.4%, plumbing 99.0%.

## 5. Precision review sample (100 random positive hits)

Reviewed by hand in `docs/reports/stage2-precision.md`. Absence signals and phone counts are excluded here (their evidence is the list of pages checked).

| # | signal | business | evidence | page |
|---|---|---|---|---|
| 1 | limited_hours_text | Roseville Dental Associates | …and compassionate dental experience. Schedule an Appointment Office Hours Walk-ins welcome during open hours Monday – Thursday8am to 4pm FridayClosed Saturday – SundayClosed Cleaning & CheckupsRegular dental care Restor | https://rosevilledentalassociates.com/ |
| 2 | limited_hours_text | Mister Plumber Llc | …LOCATIONMister PlumberHopedale, OH 43976 PAYMENT METHODS PHONELandline: 740-937-2149Alternate Number: 740-264-7746 HOURSMon - Fri: 9:00 am - 5:00 pmSat - Sun: Closed Button Button Button Button Button Button Button OH L | https://www.misterplumberohio.com/contact |
| 3 | phone_only_booking | Frank Alvarez & Sons Plumbing and Heating | …customer service from our specially trained service technicians. Thank you in advance for choosing us, and we look forward to working with you! Please call to schedule your appointment today at 732.257.1494. CONTACT US  | https://www.frankalvarez.com/ |
| 4 | emergency_247_claim | Mvp Air Conditioning, Heating, Plumbing & Electric | …Videos Social Media Contact Contact Us Schedule Service Careers Check Out Our Available Discounts! Serving Kansas City & the Surrounding Areas 24/7 Emergency Service (913) 210-5512 Financing PROMOTIONS Schedule Service  | https://themvpkc.com/ |
| 5 | pricing_hints | Batey Brothers Heating & Cooling | …Humidifiers Smart Thermostats Products Specials Comfort Club Financing Service Area About Us Reviews Blog FAQ Contact Us AC Company in Scottsboro, AL $50 OFF OF REPAIR Call For More Details View Specials Contact Us Are  | https://callbateybros.com/ac-company-scottsboro-al/ |
| 6 | owner_operated | American Ally Drains & Plumbing | …established in Escondido, out office is now located in Ramona, and we proudly serve homeowners and businesses throughout San Diego County. As a family-owned and operated company, we believe plumbing service is about mor | https://www.americanallydp.com/about/ |
| 7 | emergency_247_claim | DC Air Heating and Cooling LLC | …heating and air conditioning, we have the tools, the equipment, and the experience to keep your equipment running smoothly all year long. If it is emergency service that you need, twenty-four hours a day, seven days a w | https://www.dcairhvac.com/ |
| 8 | emergency_247_claim | Pledge Plumbing | Expert Plumbing Services Available 24/7 - Pledge Plumbing \| PledgePlumbing.com HomeHomeExpert Plumbing Services Available 24/7Reliable solutions for all your plumbing needs, from repairs… | https://pledgeplumbing.com |
| 9 | multiple_locations | Roots Dental | …invaluable insights into your dental health and personalized treatment recommendations to restore and maintain your smile. Book Now At each of our locations, you’ll find: Experienced and compassionate dental professiona | https://www.rootsdental.com |
| 10 | multiple_locations | Esthetic Dentistry and Implants | California Dentist – Esthetic Dentistry & Implants – 7 Convenient Locations Skip to content Our office has relocated Records Request Our office has relocatedPlease visit one of these offices to book your next appointment | https://estheticdentistryandimplants.com/ |
| 11 | owner_operated | Advantage Plumbing, Inc. | …Blog FAQ About Advantage Plumbing, Inc. Outstanding Plumbing Service since 2000 Advantage Plumbing, Inc. was established in March of 2000 and is locally owned and operated. With a reputation for providing the best plumb | https://www.advantageplumbinginc.net/about-us |
| 12 | owner_operated | Columbia Cooling And Refrigeration | …installations, and preventative maintenance—all backed by honest, transparent pricing and exceptional customer care. Local Experience You Can Trust As a locally owned business, we understand the seasonal extremes of the | https://chooseccr.com |
| 13 | emergency_247_claim | Muse Heating & Cooling | Top Roachdale Heating Repair Company: Muse Heating And Cooling Skip to main content Request Service 24/7 Emergency Service Available Call: 765-522-4716 Menu Home Heating Heating Water Heaters Heat Pumps Heating Repairs H | https://www.museheatingandcooling.com/ |
| 14 | multiple_locations | Arthur Orthodontics | …appointments to accommodate your needs.DEDICATED DOCTORSWe are pleased to be your choice for orthodontic care and affordable braces. We have five convenient locations serving patients in Glendale, Chandler, Phoenix, Peo | https://www.esorthodontics.com/ |
| 15 | limited_hours_text | Breeze Dental | …concerns? Phone \| 303-752-2226Breeze Dental2226 S. Fraser St. Unit #1 Aurora, CO 80014HoursMonday - Friday: 8:00 am - 5:00 pmSaturday: By Appointment Sunday: ClosedSend MessageSend MessageName (First, Last)*Email*Phone* | https://breezedentaldenver.com/ |
| 16 | owner_operated | Greenwood Refrigeration, Inc | …refrigeration, heating, and air conditioning solutions to residential and commercial customers throughout Bowling Green, KY and surrounding areas. As a family-owned, “home town” service company with an up-and-coming sec | https://greenwoodrefrigeration.com |
| 17 | multiple_locations | Weber Refrigeration Heating & Air Conditioning | …should be left unchanged. Weber Refrigeration, Heating and Air Conditioning Call From Anywhere at Anytime 1-800-886-2188 Or Contact From One of Our Locations Below: 11154 Kliesen StDodge City, KS 67801, US (620) 225-770 | https://weberorange.com/contact-us/ |
| 18 | pricing_hints | Family Dental Care - Imelda Nagtalon DDS | …create natural-looking, secure solutions tailored to fit your lifestyle and help you eat, speak, and smile with confidence again. Custom dentures starting at $825 per arch. Learn more Dental Implants A lasting solution  | https://www.argyledentalcenter.com/ |
| 19 | pricing_hints | Elite Heating And Cooling | …Hometown Comfort Heroes. Contact Elite Heating and Cooling for expert HVAC services. Schedule Service Comfort Can Cost Less Join the Comfort Club – Starting at Just $17.25/Month! Call Today! View Details Join the Comfor | https://www.elitecomfortexperts.com/ |
| 20 | emergency_247_claim | Diamond Refrigeration, Llc. | …Refrigeration in Central Oregon top of pageWe're A Cut Above The Rest541-526-1001HOMEREFRIGERATIONHOT SIDE SERVICESHVAC & VENTILATIONICE & FILTRATIONEMERGENCY SERVICESEMPLOYMENTFAQABOUTCONTACTMoreUse tab to navigate thr | https://www.diamondrefrigeration.net |
| 21 | emergency_247_claim | Llona Plumbing | …Installation Services Kitchen & Bathroom Plumbing Schedule Service Gas Lines & Emergency View All Services → Residential Gas Line Installation & Repair Emergency Plumbing Services Residential Gas Line Installation & Rep | https://llonaplumbing.com/ |
| 22 | emergency_247_claim | Total HVAC | …totalhvac.sarah@gmail.comOur Regular Business Hours - call for appointmentOpen today08:00 am – 04:00 pmAny hours outside of the above hours are considered emergency service unless previously scheduled. We offer 24/7 eme | https://totalhvac.org/ |
| 23 | emergency_247_claim | Call Dad | …issue and perform all the necessary repairs to get your systems up and running.‍Rely on us when you need routine or emergency HVAC services. We’re available 24/7 with emergency and holiday services.Call Dad AC & Heating | https://www.calldad.com/locations/hickory-north-carolina |
| 24 | emergency_247_claim | Dyson Plumbing | …Repair and ReplacementWater Heater RepairWater Heater MaintenanceWater Heater ReplacementRepair or Replace a Water Heater?Video Pipe InspectionEmergency PlumbingBaldwin County, AL PlumberPlumber in Point Clear, ALEaster | https://www.dysonplumbing.com |
| 25 | emergency_247_claim | Staples Ronald C Dds | …team you can trust. Whether you are interested in learning more about dental implants to restore your smile or need the help of an experienced emergency dentist near you, you’ll feel comfortable and welcome in our offic | https://www.momsg.org/ |
| 26 | emergency_247_claim | Complete Smiles of Hillsborough | …919-732-8584 Menu Home About Us Meet Dr. Davia Nickelson Our Technology Community Involvement Services General Dentistry Dental Exam & Cleaning Emergency Dentistry Extractions Fillings Sealants Pediatric Dentistry Oral  | https://www.completesmileshillsborough.com |
| 27 | emergency_247_claim | Air Electric Plus | …LLC, our state-certified technicians are committed to quality workmanship, honest recommendations, and excellent service. From installations to emergency repairs, we get the job done right. Get Free Estimate FAQ Frequen | https://airelectricplus.com |
| 28 | owner_operated | Sicilia Heating & Cooling | …delivering exceptional service to provide you with top quality service. My BlogSicilia Heating and Cooling has been in business since 1975. We are a family owned company with honest and highly skilled employees. Social  | https://sicilia-hvac.com/ |
| 29 | emergency_247_claim | Marty's Heating & Air | …dealer call us to install or repair your home heating and air conditioning system. We offer free estimates for all new equipment installations and 24/7 emergency repair services to take care of all your HVAC needs.When  | https://www.martysheatingandair.com/ |
| 30 | emergency_247_claim | SOS Water Heater Mckinney TX | …we offer a comprehensive range of hot water heater services to homeowners in Mckinney, and the surrounding areas. 100% Satisfaction Guaranteed 24/7 Emergency Service The speediest Response and Qualified Staff Offer Affo | https://soswaterheatermckinney.com |
| 31 | owner_operated | Total Plumbing | …trusted partner for plumbing repairs, installations & maintenance! FollowFollowFollow Menu Home About Services Our Work Contact Contact Us As a locally owned and family-operated business, we take pride in providing expe | https://totalplumbingmo.com/ |
| 32 | emergency_247_claim | Rapid Rooter Plumbing | West Michigan Septic and Drain Services \| Rapid Flush Skip to content Open 24/7 nights, weekends and holidays! Facebook Linkedin Instagram Tiktok Youtube Call Today 616.842.5535 Book Appointment Home Sewer & Drain Servic | https://rapidflush.com/ |
| 33 | multiple_locations | HVAC Engineering | …our company or our services please use our information request form or call the office closest to you. Need service? Give us a call at: (629) 202-4371 Locations Corporate Office624 Grassmere Park, Suite 8Nashville, TN 3 | https://www.nationalhvacservice.com/contact-us |
| 34 | owner_operated | Pineapple Plumbing - Honolulu | …DetectionSlab Leak RepairDrain and SewersDrain CleaningHydro JettingMain Water Line RepairSewer Line RepairSewage Ejector PumpsContact(808) 202-2939Family-Owned Plumbing CompanyService Beyond ExpectationWelcome to Pinea | https://honoluluplumbing.co |
| 35 | phone_only_booking | Lima Dental | …philosophy. CURRENT PROMOTIONS AND SPECIALS: The Following are for patients with no insurance: $99 Adults Exam and x-rays (13 yrs and older) Call Now to make an appointment 956-458-8144 Specialities Dental Care Services | https://limadentalrgv.com |
| 36 | multiple_locations | Brick Plumbing | …were the most reasonable from other two companies that we had quotes from. He was great that we now use him for all of our Plumbing needs at all our locations. I even used him personally! I highly recommend him wether i | https://www.brickplumbingtx.com/ |
| 37 | emergency_247_claim | Just in Time Heating and Air LLC | …Contractor FollowFollow (775) 423-6519 Request Service HOME About Us Residential HVAC Commercial HVAC Electrical Services Contact US Careers Emergency Service HVAC FAQs Trusted Heating & Air Conditioning Services Emerge | https://jithvac.com |
| 38 | emergency_247_claim | Stuart Hvac, Llc | …experience. Our full HVAC services include maintenance, repair, replacement and professional installation of all kinds of new systems. Call Stuart HVAC, day or night, if your air conditioning unit or cooling systems hav | https://stuart-hvac.com/ |
| 39 | emergency_247_claim | Apollo Heating, Cooling, & Plumbing | …homes, something eventually does, there’s no waiting it out.Apollo Heating, Cooling & Plumbing provides repair, installation, maintenance, and emergency service for every major heating system type across Akron and Summi | https://gotoapollo.com/heating-services/ |
| 40 | emergency_247_claim | Minuteman Sewer And Drain | …Minuteman service quote Name(Required) First PhoneEmail UntitledCAPTCHA P: 517-783-9952E: contact@minutemansewer.comHours: Mon - Sun \| 8A - 4:30P24hr Emergency ServiceServicesHOME VIDEO INSPECTION JETTING DRAIN CLEANING | https://minutemansewer.com/ |
| 41 | multiple_locations | Eurs Michael Dds | …staff who are compassionate, understanding and kind. You should definitely go here.-Morgan New Page welcome Best Teeth about Services Testimonial Our Locations Capitola Family Dentistry 820 Bay Avenue Suite 250Capitola, | https://www.capitolafamilydentistry.com/ |
| 42 | owner_operated | Vertex Services LLC | …Now Residential Commercial Water Heaters Sewer & Drain Emergency Service Area About Get 24/7 Help Now VERTEX SERVICES Plumbing Experts Petal, MS Locally Owned 24/7 Emergency Service Licensed & Insured Mississippi's Trus | https://www.vertex-servicesllc.com/ |
| 43 | limited_hours_text | Ogden Heating and Air | …696-4476 Chat with us (918) 696-4476 ogdenshvac@outlook.com Quick Links About Services Gallery Contact Business Hours Mon - Fri 8:00 am - 5:00 pm Sat - Sun Closed Follow Us © Ogden's Heat & Air POWERED BY Share by: | https://www.ogdenshvac.com |
| 44 | pricing_hints | Comfy Dental Studio of Beverly Hills | …a call(310) 499-2048 ComplimentarySecond OpinionsA second opinion can bring clarity and confidence to your dental decisions. Schedule Appointment $1000Off Smile MakeoversOn Crowns, Veneers and All-on-X. Schedule Appoint | https://www.comfydentalstudios.com/ |
| 45 | owner_operated | Armoured Plumbing, Inc. | …Armoured Plumbing so popular?Armoured Plumbing is renowned for its exceptional plumbing services in the Greater Sacramento Area. Since 1981, this family-owned business has been providing fast and reliable plumbing servi | https://www.armouredplumbing.com/ |
| 46 | limited_hours_text | St. Barnabas Dental Medicine | …Details Headquarters 5850 Meridian Rd Gibsonia, PA 15044 724-443-0700 Working Hours Monday to Friday: 9:00 AM – 5:00 PM Saturday: 10:00 AM – 2:00 PM Sunday: Closed Corporate Career Center 5827 Meridian Rd. Gibsonia, PA  | https://stbarnabashealthsystem.com/contact/ |
| 47 | pricing_hints | Performance Plumbing | …thing you’ll never get when hiring us is sticker shock when you get your invoice because we give you a flat-rate price instead of charging by the hour. $25OFF First TimeCustomers Get This Deal $50OFF Water HeaterRepair  | https://performance.plumbing/ |
| 48 | emergency_247_claim | Rodney Nelson, Dds | …Membership Pay Your Bill Patient Portal Careers Contact Blogs General Dentistry Preventive Dentistry Tooth-Colored Fillings Oral Cancer Screening Emergency Dentistry Gum Disease Treatment Extractions Restorative Dentist | https://cfdokc.com/ |
| 49 | emergency_247_claim | Fuller Smiles - Dentist - Rialto | …procedures performed, well over 14 million every year.Know more Knocked-out teeth, a mouth wound, or a cracked tooth should all have you headed to an emergency dentist right away. Fuller Smiles’s dental experts in Manha | https://fullersmiles.com |
| 50 | pricing_hints | ADR Mechanical LLC | …fantastic offer and elevate your experience with our top-of-line technology. Don't miss out on the opportunity to invest in quality and performance! $20 Off Service CallGet $20 off your next service call! Take advantage | https://www.airdonerighttx.com |
| 51 | limited_hours_text | Extreme Plumbing N.y. Inc. | …Links About Us Gallery Financing (opens in a new tab)Referral Program Areas Served Testimonials Blog Contact Contact Us Mon to Fri: 9AM - 5PM Sat & Sun: Closed 24/7 Emergency Services Available 3048 Merrick Rd, Wantagh, | https://extremeplumbingny.com |
| 52 | emergency_247_claim | Snikeris Family Dentistry | …Woodlands, TX \| Snikeris Family Dentistry 0 Skip to Content About Us Services Preventive Dentistry Restorative Dentistry Cosmetic Dentistry Emergency Dentistry Dentists Team Patients Appointments Patient Portal In-Netwo | https://www.snikerisfamilydentistry.com/ |
| 53 | emergency_247_claim | Advanced Plumbing And Drain | …Plumbing & Drain MENU Watch the video Home Plumbing Services Hydro-Jetting Water Leak Detection Toilet Repair & Installation Pipe Leak Repair Emergency Services Commercial Plumbing Sewer & Drain Sewer Repair Sewer Repla | https://advancedplumbingsf.com |
| 54 | emergency_247_claim | Drain Right Plumbing | …CA; Torrance, CA; and the rest of Orange County. No matter when you need our plumbing, drain, or HVAC expertise, you can call Drain Right Services day or night for fast, reliable results. We Do Drains Right When you cal | https://www.drainrightservices.com |
| 55 | owner_operated | Norris Mechanical Shop Inc | …Home About Us Services Maintenance Products Financing FAQ Testimonials Contact Us Quality AC Repair in El Dorado AR Norris Mechanical Shop is a family-owned HVAC company dedicated to the good folks in the El Dorado, AR, | https://norrismechanicalshop.com/ |
| 56 | emergency_247_claim | Cliff And Sons | …Mitsubishi Mini Splits Rheem Water Heaters Cliff & Sons Heating & Cooling HVAC SERVICES ABOUTCONTACTQUOTE BUSINESS HOURS Mon - Fri 8am - 4:30pmEmergency Service Available LOCATION 758 E Chicago RdQuincy, MI 49082 CONTAC | https://www.cliffandsons.com/ |
| 57 | owner_operated | Plumb Krazy Plumbing | …Krazy Plumbing \| Your Commercial Plumbing Contractors HOME ABOUT US SERVICES CONTACT US BLOG  877-628-7473 HOME ABOUT US SERVICES CONTACT US BLOG Family Owned & Operated Since 2010 THE TRUSTWORTHY COMMERCIAL PLUMBERS P | https://plumbkrazyplumbing.com/ |
| 58 | emergency_247_claim | Talon Mechanical | …Metro HVAC Repair Done Right The First Time AC repair, AC replacement, heat repair, heat replacement, HVAC maintenance, commercial HVAC, and emergency service across the Portland metro, with a repair-first diagnosis ins | https://talon-mechanicalhvac.com/ |
| 59 | emergency_247_claim | SIR Heating and Air Conditioning, LLC | …to serving you. SIR HEATING AND AIR CONDITIONING, LLC Millington, MI 48746 CALL USPhone: (586) 531-3402Phone: (810) 441-1514Phone: (989) 545-935024/7 EMERGENCY SERVICES AVAILABLEHOURSMon: 8AM - 5PMTue: 8AM - 5PMWed: 8AM | https://sirhvac.com/contact/ |
| 60 | owner_operated | Service Plus Heating, Cooling, Plumbing & Electrical | …Electrical Repair Electrical Panel Upgrade Electrical Safety Inspection Lighting Installation Contact Us Service Plus Club Book Your Service! Indy’s Family-Owned & Locally Operated Heating, Cooling, Plumbing, Electrical | https://serviceplusnow.com |
| 61 | multiple_locations | Robertson Heating Supply | …has grown into one of the region’s leading distributors of plumbing, HVAC, hydronics, and kitchen cabinetry products serving professionals across 40 locations in five states: Ohio, Michigan, Pennsylvania, West Virginia, | https://www.rhs1.com/about.php |
| 62 | emergency_247_claim | Mainland Mechanical | …night. Regular use could exhaust your heating and cooling systems, increasing your risk of unplanned breakdowns. We provide quick and dependable emergency repair services for heat pump repair Galloway NJ, furnaces, boil | https://mainlandmechanicalnj.com/ |
| 63 | emergency_247_claim | Nova Temperature Control LLC | …fluctuatingYour unit making odd soundsIf you've noticed any of these signs in your home, call 703-425-1081 ASAP to schedule HVAC repairs. We offer 24/7 emergency repair services for residents in Dunn Loring and Fairfax, | https://nova-tempcontrol.com/general-service/ |
| 64 | owner_operated | AMW Cooling & Heating LLC | …Texas Department of Licensing and Regulation (TDLR) under license number TACLB133920E.When you choose AMW Cooling & Heating LLC, you're choosing a family-owned business dedicated to providing efficient, effective soluti | https://amwairconditioning.com/about |
| 65 | pricing_hints | Denton Family Smiles, Sleep & TMJ | …offer targeted sleep apnea and TMJ treatments, including custom-crafted sleep devices designed to restore your quality of life. New Patient Offer $79 Cleaning, Exam, & X-Ray* Welcome to your new dental home! Start your  | https://dentist-denton.com |
| 66 | limited_hours_text | Periodontal Specialists | …695-6444 Fax: (810) 695-4414 Grand Blanc Hours: MondayCLOSED Tuesday8:00 AM - 5:00 PM Wednesday8:00 AM - 5:00 PM Thursday8:00 AM - 5:00 PMFridayCLOSEDSaturdayCLOSEDSundayCLOSED Contact Us We encourage you to contact us  | https://www.periodonticsonline.com/contact-us/ |
| 67 | phone_only_booking | Sutton's Heating & Cooling | …CashCheckFinancing with approved creditCredit Cards 406-777-2665 Contact UsCall Now Contact Us The Furnace Guys is here to help. Please, give us a call to make an appointment for service. The Furnace Guys177 N BroadwayV | https://www.suttonheatingandcooling.com/about/contact-us/ |
| 68 | owner_operated | Royal Flush Septic Pumping | …Flush Septic Service, you will be working with a company that really cares, a company that offers the personalized attention and understanding of a family owned business. If you need advice on septic tank pumping freque | https://royalflushseptic.com |
| 69 | emergency_247_claim | Hot & Cold Air Co. | …Service apply.Hot and Cold Air CoServing East TennesseeCall or Text 865-321-0152 Email: info@hotandcoldairco.comHoursMonday - Friday 7 a.m. - 5 p.m.24/7 Emergency Services Available Depending on Availability. SubscribeS | https://hotandcoldairco.com/ |
| 70 | multiple_locations | Contemporary Endodontics Sugar Land | …Experiencing Pain?Contact Us TodayWelcome to Contemporary Endodontics Contemporary Endodontics is a patient-oriented specialty dental practice with multiple locations in and around Houston, Texas. Our caring Dental Team | http://www.ceteamendo.com |
| 71 | emergency_247_claim | J.Fries Heating & Cooling | …Heating & Cooling offers a full range of HVAC services, including heating and cooling system repairs, new installations, seasonal maintenance, and emergency services. We provide services for furnaces, air conditioners,  | https://www.jfriesheatingandcooling.com |
| 72 | multiple_locations | Valley Dental | …they are really good with appointment reminders and confirmations. Only place I have consistently visited for cleaning. - Will T. p Get in Touch Two Locations For Your Convenience Walnut 909-598-2763 20266 Carrey Rd. Wa | https://www.myvalleydental.com |
| 73 | multiple_locations | Rosslyn Dental Arts Center | …strategically placed offices in South Arlington, Rosslyn, Crystal City to ensure that high-quality dental care is always within reach. Discover our locations and see how we can help you achieve your best smile. Rosslyn  | https://smileperfectors.com/about-us/ |
| 74 | owner_operated | Tom W. Fox Jr. DDS | …1987. Get in touch with us to schedule an appointment today! Why Tom W. Fox, Jr., DDS Is Your Best Choice ADA Approved Over 42 Years of Experience Locally Owned Dental Clinic Senior and Other Discounts Available Most Ma | https://www.tomfoxjrdds.com |
| 75 | emergency_247_claim | Advanced Dentistry of Citrus County | …variety of cosmetic dental treatments to enhance your beautiful smile.Cosmetic Dentistry servicesSmile MakeoversTooth BondingDental Veneers Emergency DentistryEmergency dental care when you need it most. We're here to r | https://advanceddentistryhomosassa.com/our-services |
| 76 | emergency_247_claim | Maine Dentistry | …noticing, or find a topic below that sounds familiar.Explore dentures and Auburn consultations Dental pain or an unexpected problem? Explore emergency dentistry Explore your options Talk with our team A conversation abo | https://www.mainedentistry.com/services/ |
| 77 | multiple_locations | Macdonald Orthodontics | …appointment to our 4th or 5th visit.Moe A.The doctor & staff here are amazing!!! I love the business marketing, it is fabulous for everyone. Reviews Our locations Come Visit Our Smiling Team With three prime locations a | https://macdonaldortho.com/ |
| 78 | emergency_247_claim | A Air Company | …prepares individualized contracts tailored to meet the client’s maintenance requirements including: labor, parts, preventive maintenance and a 24-hour emergency service. We offer the following services: MECHANICAL SERVI | https://www.a-air.net/services.htm |
| 79 | phone_only_booking | Schroyer Dental | …crowns, bridges and implant restorations are placed in the office.Emergency Dentistry Emergency patients are typically seen the same day. Please call for an appointment .Invisalign Aligners Adult orthodontic clear align | https://www.schroyerdental.com/ |
| 80 | limited_hours_text | Carlisle Dental Studio | …7:00 a.m. – 3:00 p.m. TUESDAY 8:00 a.m. – 4:30 p.m. WEDNESDAY: 7:00 a.m. – 3:00 p.m. THURSDAY: 7:00 a.m. – 3:00 p.m. FRIDAY: By Appointment SAT& SUN: CLOSED Recent Articles Use of a intraoral scanner in the dental pract | https://carlisledentalstudio.com/ |
| 81 | emergency_247_claim | John Patterson, DMD | …More View More Common Questions Find clear, honest answers about our treatments, appointments, insurance, and what to expect during your visit. 24/7 Emergency (480) 280-6170 How often should I visit the dentist? We reco | https://drjohnpatterson.com/ |
| 82 | emergency_247_claim | Smiles Family Dental - Flower Mound | …WhiteningGeneral DentistryDental CleaningsGum Disease TreatmentScaling and Root PlaningSleep Apnea TreatmentTooth ExtractionsWisdom Tooth ExtractionsEmergency Dental CareSedation DentistryRestorative DentistryDental Bon | https://www.smilesfamilydental.com/ |
| 83 | owner_operated | Emerald Coast Air Conditioning And Heating | …different businesses in the search results. What sets Emerald Coast Air Conditioning and Heating apart from other HVAC companies? We are a small, family-owned and operated company with over 20 combined years in the HVAC | https://airconditioningrepairpensacola.com |
| 84 | pricing_hints | Professional Services | …Houses, Co-op & Condo Apartments, Villas, Townhouses, Commercial spaces, and Mixed-use structures. Typical renovation projects range in size and cost from $25,000.00 - $1,000,000.00. Most of our business in New York cam | https://www.professional-services.com/ |
| 85 | emergency_247_claim | Customer Air Conditioning & Air Quality Llc | …Air Conditioning Heating Heating Installation Heating Repair and Replacement Heating Service and Maintenance Maintenance Plan Other Services Emergency Services Heat Pumps Ductless Ductwork Ductwork Installation Duct Cle | https://www.customacquality.com/ |
| 86 | uses_answering_service | My Local Plumber | …the neighborly approach that makes our service unique. Book Now Call 972-418-1421 More Than Just Plumbing Serving North Dallas means more than answering service calls. It means giving back, supporting local initiatives, | https://mylocalplumber.net/about-us/ |
| 87 | emergency_247_claim | South Shore Plumbing | …ServicePlumbing TipsPartnersContactCareersBlogMoreUse tab to navigate through the menu items.SOUTH SHORE PLUMBING TO BE SURE.Contact UsSchedule ServiceOur Services 24/7/365Sewer and Drain CleaningRun power snake to clea | https://www.southshoreplumbing.com/ |
| 88 | emergency_247_claim | Harmon Heating & Cooling, Inc | …133rd CtOmaha, NE 68137Contact Person: Dave Harmondaveh@omhcoxmail.com402-551-3531 Office Hours Mon - Fri 8:00 am - 5:00 pm Sat - Sun Closed 24-hour emergency services. Get Directions We are proud to serve the entire ar | https://www.harmonheatingandcooling.com/contact-us |
| 89 | emergency_247_claim | C & S Plumbing, Inc. | Waxahachie Plumber \| CSInc. plumbing \| 24/7 Emergency Skip to content 24-hour emergency service Licensed & insured · Lic. M-36322 Waxahachie & Ellis County, TX 972-351-9041 CSInc.plumbing & mechanical… | https://www.csiplumbing.com |
| 90 | hiring_receptionist | Straight Shooter Plumbing and Rooter | …service? Straight Shooter Plumbing and Rooter LLC is growing fast, and we’re looking for a friendly, organized, and motivated Customer Service Representative (CSR) to join our team as the first point of contact for our  | https://www.straightshooterplumbing.com/who-we-are/careers/ |
| 91 | emergency_247_claim | Loikits Oil & Heating Co. | …serving Lehigh Valley families and businesses with quality products and services since 1939.​Loikits Oil is a full service company. We offer 24 hour emergency service exclusively for our oil customers, Automatic or Will | https://www.loikitsoil.com/ |
| 92 | emergency_247_claim | Hensinger Plumbing | …without any hassle. Call us at 260-347-3130 for more details. Learn More Take Advantage of Many Brands You'll be pleased to know that we offer emergency services for our air-conditioning, water conditioning, water heate | https://www.hensingerplumbing.com/ |
| 93 | emergency_247_claim | A To Z Plumbing & Heating Inc | …Page For More Info Excellent Plumbing Fixtures Excellent Plumbing Fixtures In Business Since 1999 Learn More Hassle-Free Gas and Heating System Services 24/7 Emergency Services are Available Learn More First-Class Centr | http://www.atozplumbingandheating.net |
| 94 | owner_operated | AAAlways Plumbing | …offer next-day service. How much experience does your team have? Our crew brings 50 years of combined plumbing experience, and we’re proud to be locally owned and operated right here in Spring. We’ve built our reputatio | https://aaalwaysplumbing.com/ |
| 95 | multiple_locations | West Chester Endodontics | …InteractionOur DifferenceOur team of dedicated professionals goes above and beyond to provide personalized service. With emergency care, four convenient locations, and high-quality service, we strive to treat your needs | https://www.precisionendonetwork.com/ |
| 96 | limited_hours_text | Apex Endodontics | …624-7000 Office Hours Monday8:30 am - 5:00 pm Tuesday8:30 am - 5:00 pm Wednesday8:30 am - 5:00 pm Thursday12:30 pm - 7:00 pm Friday8:30 am - 5:00 pm SaturdayClosed SundayClosed Monday8:30 am - 5:00 pm Tuesday8:30 am - 5 | https://apexrootcanal.com/appointments/ |
| 97 | emergency_247_claim | S&S Mechanical | …OptionsIndividualized PLUMBING, HVAC, & ELECTRICAL Options Your comfort and safety are our top priorities. Whether it's a routine maintenance check, an emergency repair, or any heating, cooling, and ventilation needs in | https://ssfirstmechanical.com/ |
| 98 | emergency_247_claim | Ckc Contracting Inc | …Loading... THE TWIN CITIES EXCAVATION & SITE UTILITIES EXPERTS The Twin Cities Excavation ExpertsOrangeBall Creative2024-11-20T13:47:06-06:00 Emergency Service COMMERCIAL & INDUSTRIAL EXCAVATING CERT SBE SITE UTILITIES  | https://ckccontracting.com/ |
| 99 | emergency_247_claim | Metropolitan Drain Cleaning | Drains Great \| Aurora, Centennial & Denver, CO \| Plumbing Services Home Drain Cleaning Plumbing and Services Emergency Plumbing Water Heaters Garbage Disposals Toilet Repair Toilet Installation Leak Detection Sewer Line  | https://www.drainsgreat.com/ |
| 100 | emergency_247_claim | T. Dotson 's Draintechs | …team committed to professionalism, customer satisfaction, and dependable service every time. Drain Cleaning & Septic Services in Mountain Top, PA 24/7 Emergency Drain & Septic ServicesDon’t wait when a drain or septic e | https://www.draintechinc.net |

## 6. Projection

| measure | pilot | at 3,000,000 businesses (~2,340,000 sites) |
|---|---|---|
| wall time | 52 min for 15,399 sites | ~132 h at the same concurrency |
| download | 6.87 GB (205,449 B/page) | ~1.0 TB |
| cost | $0 | $0 in fees; compute + bandwidth only |

Saved HTML: `/home/user/Receptionist-saas/leads/data/crawl/pages` (gzipped Parquet, gitignored).
