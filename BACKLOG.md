# Feature backlog

Ideoita joita ei ole vielä toteutettu. Järjestys ~prioriteetin mukaan, ei kova pakko.

Kiinnostavin = "miksi siirtyä reaktiivisesta proaktiiviseen". Suurin oppimisarvo = "miten agentit oikeasti tekevät yhteistyötä".

**Toteutetut alunperin samasta listasta:**
- ✅ #3 Thought traces (Ajatus-rivit subagenteilla, 💭 Perustelut LEADilla) — PR #18, #20, #21

---

## #1 Plan-then-execute LEADilla  *(keskisuuri)*

LEAD ei tällä hetkellä todellisuudessa "ajattele" suunnittelu-tasolla — se vain reitittää
classify_query:n kautta ja synteseuraa. Lisää välivaihe ennen subagenttien dispatchia:

1. Router luo karkean classification (nyk.)
2. **UUSI** — LEAD kirjoittaa **strukturoidun suunnitelman**:
   - Per subagentti: tarkennettu kysymys / fokus
   - Mahdollisesti agenteittain pudotuksia ("skip sentiment, kysely on puhtaasti numerollinen")
   - Mahdollisesti lisäyksiä ("kysymys vaatii myös portfolio-näkökulman vaikka router ei nostanut")
3. Subagentit ajetaan plan-tasolla räätälöidyillä prompteilla
4. Synteesi (nyk.)

UI näyttää suunnitelman omana blokkinaan **🧠 Suunnitelma** ennen subagenttien
toimintaa — käyttäjä näkee ennen kuin mitään ajetaan, mitä LEAD aikoo tehdä.

**Riippuvuus:** rakennettu PR #21:n LEADin "💭 Perustelut" -callouten päälle, ehkä jo
asettelu valmiina.

**Riski:** ylimääräinen LLM-kutsu hidastaa — kannattaa tehdä rinnakkain reitityksen
kanssa tai käyttää nopeaa Flash-mallia.

---

## #2 Reflektio + retry kun output on outoa  *(keskisuuri)*

Subagentit antavat joskus järjenvastaisia vastauksia (negatiivinen CAGR positiivisilla
arvoilla, tyhjät vastaukset, vain "data ei saatavilla", anomalous numbers). Lisää
post-processing -kerros joka:

1. Tarkistaa output:n red flag -kriteereillä:
   - Tyhjä / "_(empty response)_"
   - "ei saatavilla", "couldn't compute", "N/A" -tyyppisiä fraaseja
   - Numeerinen poikkeavuus (esim. CAGR > 100% tai < -50%)
   - Vain Ajatus-rivi, ei vastaussisältöä
2. Jos red flag -> **retry samalla agentilla** mutta lisätyllä kontekstilla:
   *"Edellinen vastauksesi sisälsi: [output]. Tarkista onko järkevä, jos ei niin
   vastaa toisin. Jos on, perustele miksi tämä numero / fraasi pitää paikkansa."*
3. Cap retry määrä 1:een per agentti (kustannukset, pingvinin loop)
4. UI näyttää retry-tilan: "▲ QUANT — REFLEKTOITU (alkuperäinen vaikutti
   epätavalliselta)"

**Riippuvuus:** toimii itsenäisesti, mutta arvokkaampi kun yhdistetty plan-then-execute:n
kanssa (LEAD näkee retry:n ja voi adapt synteesin).

**Riski:** retry voi maskata oikeasti puuttuvan datan oletuksilla — pitää erottaa
"ei dataa" (rehellinen) vs "outo data" (tarkista).

---

## #4 Watchlist + päivittäinen briefing  *(iso, eri päätelaite)*

Käyttäjä voi sanoa "seuraa Sampoa". Tallennetaan watchlist `~/.inderes_agent/watchlist.json`.

GitHub Action ajaa joka aamu:
- Hakee jokaiselle watchlistatulle yhtiölle "mitä uutta?"
- Käy läpi: uudet analyytikkonotet, insider-kaupat, tulosjulkistukset, foorumipiikit
- Tallentaa yhden markdown-tiedoston per päivä

Streamlitiin uusi sidebar-osio "📅 Aamun briefing" josta käyttäjä avaa päivittäisen yhteenvedon. Nappi "Päivitä nyt" forssaa heti.

**Miksi iso:** Siirtää koko tuotteen reaktiivisesta proaktiiviseen. Mind shift. Vaatii: scheduled job, watchlist-store, brief-generaattori, UI-osio.

**Riippuvuudet:** Toimii ilman muita featureita.

---

## #5 Pitkäkestoinen muisti — insight ledger  *(keskisuuri)*

Nyt `ConversationState` muistaa vain `last_companies` ja `last_summary`. Häviää sessiolopussa.

Lisää **insight ledger**: jokaisesta kyselystä LEAD destilloi 1-3 muistettavaa havaintoa
(esim. *"Sammon Q3 odotettua heikompi", "UPM:n insider-myynnit lisääntyneet"*).
Tallennetaan persistenttisti `~/.inderes_agent/insights.jsonl`.

Seuraavalla kyselyllä relevantit (matchaa company tai toimiala) ladataan kontekstiin
→ agentti rakentaa **kasvavan yritystuntemuksen** sen sijaan että tabula rasa joka kerta.

Reuna-tapaukset:
- Insightin vanhentumislogiikka (vanha tieto voi olla väärin nyt)
- Kuinka monta insighttia kontekstiin? (token-rajat)
- Käyttäjä voi nähdä/poistaa/muokata insighteja UI:ssä?

**Riippuvuudet:** Hyödyllisin yhdistettynä #1 plan-then-executeen — plan voi viitata
muistettuihin insighteihin.

---

## #6 Disagreement surfacing  *(keskisuuri)*

Kun QUANTin numerot ja Inderesin estimaatit ovat ristiriidassa (esim. P/E 12x vs analyytikon 18x),
älä piilota sitä — **nostaa esiin** synteesissä omana osionaan **🚨 Ristiriidat**.

Toteutus:
- LEADin synteesi-prompt sisältää eksplisiittisen ohjeen: "Jos subagenttien tiedot
  ristiriitaisia, kirjaa Ristiriidat-osio."
- Vaihtoehtoisesti: post-processing joka diff-tarkistaa numeerisia ristiriitoja
  per metric

**Miksi arvokas:** Lisää luottamusta. Käyttäjä näkee mistä ollaan epävarmoja sen
sijaan että saa konfidenssikuvan vääristä numeroista.

**Pikavoitto-versio:** Lisää LEAD-promptin ohje, ei muutoksia koodiin. ~30 min.

---

## #7 Subagent-to-subagent kutsu  *(iso, arkkitehtuurimuutos)*

Nyt arkkitehtuuri yksitasoinen: LEAD → subagent → MCP. Jos QUANT tarvitsee sentimenttitietoa,
sen pitäisi pyytää LEADia ja LEAD dispatcheaa SENTIMENTin uudella roundilla.

**Suora subagent-to-subagent**: QUANT voi kutsua SENTIMENTtia ilman LEADin välityskäyntiä.

Haasteet:
- Infinite loop -esto
- Per-kutsu kustannuskatto (max N hyppyä)
- Kuka näkee mitä? (privacy / context isolation)
- Tracing — kuka kutsui ketä, kun?

**Miksi opettavainen:** Klassinen multi-agent system challenge. Pakottaa miettimään
koordinaatiota, dependency injectionia, costsia.

**Riippuvuudet:** Pitäisi mahdollisesti tehdä koordinaattorina toimiva *team lead*-agentti
ennen tätä, jotta arkkitehtuuri on selkeä.

---

## #8 Debate-arkkitehtuuri  *(iso)*

Tietyille kysymyksille ("kannattaako Sampoon ostaa?") spin up **kaksi vastakkaista
agenttia** — *bull* ja *bear* — jotka argumentoivat saman datan pohjalta vastakkaisia
näkemyksiä. LEAD tai erillinen *judge*-agentti tasapainottaa molemmat synteesissä.

Käyttötapaukset:
- Sijoituspäätös-tyyppiset kyselyt
- "Onko tämä yhtiö liian arvostettu?"
- Arvon vs kasvun debatti
- Tase-riskien arvostus

Toteutus:
- Uudet promptit `bull.md`, `bear.md` (samalla tool-setillä kuin RESEARCH)
- Router tunnistaa decision-tyyppisen kyselyn → spawnaa bull + bear
- Synteesi näyttää molemmat argumentit ennen omaa neutraalia kokoavaa vastausta

**Miksi arvokas:** Lisää intellectual rigor. Käyttäjä näkee oikeasti molemmat puolet
ennen omaa päätöstään.

---

## Pienempiä ideoita / nice-to-have

- **Suggested follow-ups**: Synteesin loppuun "💡 Voisit kysyä myös: ..." 3 ehdotusta
- **Confidence scoring**: Jokainen subagentti raportoi 1-5 confidence per claim
- **Source provenance**: Jokainen claim synteesissä → eksplisiittinen tool-call-viittaus
  (`(get-fundamentals/Sampo, 2025)`)
- **Web search -työkalu RESEARCHille**: Pull recent news context (esim. Reuters/Bloomberg)
- **PDF-raportit**: "Vie tämä kysely PDF:ksi" → matplotlib-charts + tableat + analyysi
- **Historiallinen backtest**: "Mitä olisit suositellut 3kk sitten Sammosta?" — agentti
  rajaa kontekstinsa siihen päivämäärään ja katsoo nyt miten ennuste osui
- **Streaming output**: Token-by-token vastauksen renderöinti chat-bubblessa
- **Feedback loop**: 👍/👎 jokaisen vastauksen alla, kerätään dataa promptien parantamiseksi
- **Konfliktinratkaisu kun MCP-data puuttuu**: Tällä hetkellä joskus QUANT raportoi "data
  ei saatavilla" mutta LEAD ei tiedä että puuttuu. Eksplisiittinen "missing data"-flag
  subagent-vastauksissa, LEAD ottaa huomioon synteesissä.
