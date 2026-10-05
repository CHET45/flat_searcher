from unittest import TestCase

from flat_searcher.shortlist.facts import (
    LAYOUT_RANK,
    heating_fact,
    hot_water_fact,
    land_fact,
    layout_fact,
    sells_share,
    stove_fact,
)


def _flat(description: str, rooms: int | None = 2, series: str = "P. kara") -> dict:
    return {
        "core": {"declared_rooms": rooms, "building_series": series},
        "fields": {"Sērija": series},
        "text": {"title": "", "description": description},
    }


class LayoutFactTests(TestCase):
    def assertLayout(self, description: str, value: str, source: str = "text", **kwargs) -> None:
        fact = layout_fact(_flat(description, **kwargs))
        self.assertEqual((fact.value, fact.source), (value, source), description)

    def test_isolated_rooms_in_latvian_and_russian(self) -> None:
        self.assertLayout("Plānojums: 2 izolētas istabas, atsevišķa virtuve.", "isolated")
        self.assertLayout("Abas istabas ir izolētas un logi uz abām pusēm.", "isolated")
        self.assertLayout("Istabas ir lielas un izolētas, viena ar skatu uz parku.", "isolated")
        self.assertLayout("Удобная планировка: комнаты изолированные.", "isolated")
        self.assertLayout("Квартира с двумя изолированными комнатами.", "isolated")
        self.assertLayout("Plaša viesistaba, izolēta guļamistaba, vannas istaba.", "isolated")

    def test_walkthrough_rooms_in_latvian_and_russian(self) -> None:
        self.assertLayout("44 kvm, viesistaba caurstaigājama, vannas istaba.", "walkthrough")
        self.assertLayout("Divas caurstaigājamas istabas, lodžija.", "walkthrough")
        self.assertLayout("Две проходные комнаты, лоджия.", "walkthrough")
        self.assertLayout("Планировка: смежные комнаты.", "walkthrough")
        self.assertLayout("Комнаты смежные, санузел совмещенный.", "walkthrough")
        self.assertLayout("Virtuve-9,4m2 Istaba 1 - 16,7m2 (caurstaigājama) Istaba 2 - 13m2", "walkthrough")

    def test_one_walkthrough_room_outranks_an_isolated_one(self) -> None:
        self.assertLayout("Viena istaba izolēta, viena caurstaigājama.", "walkthrough")
        self.assertLayout("Одна комната проходная, а другая изолированная.", "walkthrough")

    def test_negated_walkthrough_is_isolated_not_walkthrough(self) -> None:
        self.assertLayout("Abas istabas izolētas (nav caurstaigājamas), ir balkons.", "isolated")
        self.assertLayout("Обе комнаты изолированные (не проходные).", "isolated")
        self.assertLayout("Istabas ir lielas un izolētas (necaurstaigājamas).", "isolated")

    def test_walkthrough_kitchen_and_courtyard_do_not_count(self) -> None:
        self.assertLayout("Virtuve ir caurstaigājama. Vannas istaba ar dušu.", "unknown", "none")
        self.assertLayout("Pagalms nav caurstaigājams. Istabas gaišas.", "unknown", "none")
        self.assertLayout("Кухня проходная. Ванная комната с душем.", "unknown", "none")
        self.assertLayout("Vannas istaba un virtuve caurstaigājama.", "unknown", "none")
        self.assertLayout("Ванная комната и кухня проходная.", "unknown", "none")

    def test_insulation_does_not_count_as_isolated_rooms(self) -> None:
        self.assertLayout("OSB plāksne, kas ir izolēta ar akmens vati.", "unknown", "none")
        self.assertLayout("Dzīvoklis ir labi izolēts no ielas trokšņiem.", "unknown", "none")

    def test_an_isolated_kitchen_is_not_an_isolated_room(self) -> None:
        self.assertLayout("Plaša halle, dzīvojamā istaba, guļamistaba un izolēta virtuve.", "unknown", "none")
        self.assertLayout("Большая прихожая, гостиная, спальня и изолированная кухня.", "unknown", "none")

    def test_series_prior_applies_only_when_the_text_is_silent(self) -> None:
        self.assertLayout("Gaišs dzīvoklis.", "walkthrough", "series", series="Hrušč.")
        self.assertLayout("Gaišs dzīvoklis.", "isolated", "series", series="602.")
        self.assertLayout("Gaišs dzīvoklis.", "unknown", "none", series="P. kara")
        self.assertLayout("2 izolētas istabas.", "isolated", "text", series="Hrušč.")

    def test_one_room_flats_have_no_layout_question(self) -> None:
        self.assertLayout("Viesistaba caurstaigājama.", "n/a", "none", rooms=1)

    def test_evidence_is_the_short_matching_passage(self) -> None:
        fact = layout_fact(_flat("Ievads. " + "Ļoti gara frāze bez punkta " * 20 + "2 izolētas istabas."))
        self.assertTrue(fact.evidence)
        self.assertTrue(all(len(snippet) <= 160 for snippet in fact.evidence))
        self.assertIn("izolētas istabas", fact.evidence[0])

    def test_rank_orders_text_over_series(self) -> None:
        ranks = [
            LAYOUT_RANK[("isolated", "text")],
            LAYOUT_RANK[("isolated", "series")],
            LAYOUT_RANK[("unknown", "none")],
            LAYOUT_RANK[("walkthrough", "series")],
            LAYOUT_RANK[("walkthrough", "text")],
        ]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        self.assertEqual(len(set(ranks)), 5)


class HeatingFactTests(TestCase):
    def assertHeating(self, description: str, value: str) -> None:
        self.assertEqual(heating_fact(_flat(description)).value, value, description)

    def test_stove_heating_phrases(self) -> None:
        self.assertHeating("Krāsns apkure, vieta vienam auto.", "stove")
        self.assertHeating("Zemesgrāmata. Apkure ar krāsni. Metāla durvis.", "stove")
        self.assertHeating("Apkure – malkas krāsns.", "stove")
        self.assertHeating("Malkas apkure, pilsētas ūdens.", "stove")
        self.assertHeating("Санузел совмещенный, отопление печное.", "stove")
        self.assertHeating("Печное отопление, центральная канализация.", "stove")

    def test_another_system_named_beside_a_stove_is_the_heating_system(self) -> None:
        self.assertHeating("Krāsns apkure vai centrālā apkure pēc izvēles.", "district")
        self.assertHeating("Отапливаться можно теплонасосом либо дровами - печное отопление.", "heat_pump")
        self.assertHeating("Для отопления предусмотрены как электрические радиаторы, так и дровяная печь.", "electric")
        self.assertHeating("Pārdošanā studio tipa dzīvoklis koka mājā ar malkas apkuri.", "stove")
        self.assertHeating("Есть дровяная печь для отопления.", "stove")

    def test_district_heating(self) -> None:
        self.assertHeating("Mājā ir centrālā apkure.", "district")
        self.assertHeating("Centrālā apkure ir individuāli regulējama, ļaujot kontrolēt izmaksas.", "district")
        self.assertHeating("Ēkā ir centrālā apkure ar individuālu siltuma patēriņa uzskaiti.", "district")
        self.assertHeating("+ Centralizēta pilsētas apkure, centralizēts aukstais ūdens.", "district")
        self.assertHeating("Siltumu piegādā Rīgas siltums.", "district")
        self.assertHeating("Квартира оборудована центральным городским отоплением.", "district")
        self.assertHeating("Дом подключён к системе центрального теплоснабжения Риги.", "district")
        self.assertHeating("Радиаторы с термостатами, городское отопление.", "district")

    def test_the_house_boiler_room_is_not_district_heating(self) -> None:
        self.assertHeating("Mājā ir nodrošināta centrālā gāzes apkure un centralizēta ūdensapgāde.", "house_boiler")
        self.assertHeating("Centralizēta gāzes apkure (ēkai pagrabā ir savs ekonomisks gāzes katls).", "house_boiler")
        self.assertHeating("Heating is provided by two communal gas boilers in the building.", "house_boiler")
        self.assertHeating(
            "Dzīvokļu apsilde ar gāzes vai cietās kurināmās vielas katlu (atrodas mājas pirmajā stāvā).",
            "house_boiler",
        )

    def test_own_boiler_and_autonomous_heating(self) -> None:
        self.assertHeating("Dzīvoklī ir uzstādīta autonoma gāzes apkure.", "own_boiler")
        self.assertHeating("Individuāla gāzes apkure, zemi komunālie maksājumi.", "own_boiler")
        self.assertHeating("Индивидуальное газовое отопление позволяет регулировать микроклимат.", "own_boiler")
        self.assertHeating("Veikti remontdarbi: - jauns gāzes katls - siltas grīdas.", "own_boiler")
        self.assertHeating("Heating is provided by an individual gas boiler.", "own_boiler")
        self.assertHeating("Gāzes katls apkurei un karstajam ūdenim, legāli izbūvēts.", "own_boiler")
        self.assertHeating("Dzīvoklī ir uzstādīts boileris un apkures katls.", "own_boiler")
        self.assertHeating(
            "Aprīkots ar boileri karstajam ūdenim, granulu katlu, kurš ir regulējams attālināti.", "own_boiler"
        )
        self.assertHeating("Автономное газовое отопление; горячая вода обеспечивается газовым котлом.", "own_boiler")

    def test_heat_pump_and_electric_heating(self) -> None:
        self.assertHeating("Apkure - siltumsūknis.", "heat_pump")
        self.assertHeating("Uzstādīts siltumsūknis guļamistabā (vasarā strādā kā kondicionieris).", "heat_pump")
        self.assertHeating("Отопление от современных электробатарей, которые программируются с телефона.", "electric")
        self.assertHeating("Electric radiators are used for heating in winter.", "electric")

    def test_a_specific_source_beats_the_word_central(self) -> None:
        self.assertHeating("Centrālā apkure. Dzīvoklim ir sava autonoma gāzes apkure.", "own_boiler")

    def test_words_that_only_look_like_a_heating_source(self) -> None:
        self.assertHeating("Dzīvoklī ir individuālie apkures siltuma skaitītāji.", "unknown")
        self.assertHeating("Each apartment has an individual heating meter.", "unknown")
        self.assertHeating("Individual heating meters are provided for each apartment.", "unknown")
        self.assertHeating("Taču pastāv visas iespējas likt siltumsūkni vai granulu katlu.", "unknown")
        self.assertHeating("City gas available, with the option to install gas heating.", "unknown")
        self.assertHeating(
            "Heating is currently provided by a stove, though electric radiators or a heat pump could be installed.",
            "stove",
        )
        self.assertHeating("Pašlaik ir krāsns apkure, kurai papildus var uzlikt elektriskos radiatorus.", "stove")
        self.assertHeating(
            "Autonomas apkures iespēja: sagatavoti izvadi granulu katla uzstādīšanai (pašlaik krāsns apkure).",
            "stove",
        )
        self.assertHeating("Elektriskā apsildāmā grīda sanitārajā mezglā.", "unknown")
        self.assertHeating("Apkure, apsaimniekošana ziemā ap 180 euro.", "unknown")

    def test_a_boiler_named_for_hot_water_does_not_heat(self) -> None:
        self.assertHeating(
            "Dzīvoklī ir gāzes katls, kas nodrošina karstā ūdens uzsildi, ir centrālā pilsētas apkure.", "district"
        )
        self.assertHeating("Karstā ūdens uzsilde ar individuālo gāzes katlu.", "unknown")
        self.assertHeating("Karstais ūdens tiek uzsildīts ar gāzes katlu.", "unknown")

    def test_evidence_quotes_the_clause_that_names_the_system(self) -> None:
        fact = heating_fact(_flat("Ir centrālā apkure. Virtuvē arī krāsns apkure."))
        self.assertEqual((fact.value, fact.evidence), ("district", ("ir centrālā apkure.",)))

    def test_a_stove_without_stove_heating_leaves_the_heating_unknown(self) -> None:
        self.assertHeating("Saglabājusies podiņu krāsns un augsti griesti.", "unknown")


class StoveFactTests(TestCase):
    def assertStove(self, description: str, value: str) -> None:
        fact = stove_fact(_flat(description))
        self.assertEqual(fact.value, value, description)
        self.assertEqual(bool(fact.evidence), value != "none", description)

    def test_stove_heating_alone_or_beside_another_system(self) -> None:
        self.assertStove("Krāsns apkure, vieta vienam auto.", "heating")
        self.assertStove("Krāsns apkure vai centrālā apkure pēc izvēles.", "heating")
        self.assertStove("Отапливаться можно теплонасосом либо дровами - печное отопление.", "heating")
        self.assertStove("Для отопления предусмотрены как электрические радиаторы, так и дровяная печь.", "heating")
        self.assertStove("Heating is provided by both electric radiators and a wood-burning stove.", "heating")
        self.assertStove("-Apkurei var uzstādīt siltumsūkni, šobrīd krāsns apkure;", "heating")
        self.assertStove(
            "Dzīvoklī ir ierīkota centrālā apkures sistēma, kuru var apsildīt kurinot jotul krāsniņu.", "heating"
        )

    def test_kitchen_appliances_are_not_stoves(self) -> None:
        self.assertStove("Cepeškrāsns, plīts virsma, mikroviļņu krāsns.", "none")
        self.assertStove("Микроволновая печь и духовка.", "none")

    def test_a_stove_object_is_present_not_heating(self) -> None:
        self.assertStove("Saglabājusies podiņu krāsns un augsti griesti.", "present")
        self.assertStove("Guļamistabā ir arī čuguna krāsniņa.", "present")
        self.assertStove("Historical details have been preserved, including a wood-burning stove.", "present")

    def test_a_removed_stove_is_not_stove_heating(self) -> None:
        self.assertStove("Bez apkures. Ir bijusi krāsns apkure, kas ir demontēta.", "present")

    def test_a_stove_that_could_be_installed_is_not_stove_heating(self) -> None:
        self.assertStove("Electric radiators, chimney flue – possibility to install a wood-burning stove.", "present")


class HotWaterFactTests(TestCase):
    def assertHotWater(self, description: str, value: str) -> None:
        fact = hot_water_fact(_flat(description))
        self.assertEqual(fact.value, value, description)
        self.assertEqual(bool(fact.evidence), value != "unknown", description)

    def test_central_hot_water(self) -> None:
        self.assertHotWater("+ Centralizēta pilsētas apkure, centralizēts pilsētas aukstais un karstais ūdens.", "central")
        self.assertHotWater("Centralizētā apkure; centralizētais karstais ūdens; koka logi.", "central")
        self.assertHotWater("Pilsētas centrālā apkure, pilsētas aukstais, karstais ūdens un kanalizācija.", "central")
        self.assertHotWater("Siltumu piegādā Rīgas siltums, aukstais un karstais ūdens - Rīgas ūdens.", "central")
        self.assertHotWater("Mājai ir centralizēta pilsētas apkure un karstais ūdens.", "central")
        self.assertHotWater("Центральное отопление; центральное горячее водоснабжение.", "central")
        self.assertHotWater(
            "Дом подключён к системе центрального теплоснабжения Риги, обеспечивающей отопление и горячее водоснабжение.",
            "central",
        )
        self.assertHotWater(
            "Karstais ūdens tiek nodrošināts izmantojot centralizēto gāzes katlu.", "central"
        )

    def test_gas_heated_water(self) -> None:
        self.assertHotWater("Karstais ūdens tiek uzsildīts ar gāzes katlu.", "gas")
        self.assertHotWater("Savs gāzes apkures katls uz dzīvokli- nodrošina apkuri un karsto ūdeni.", "gas")
        self.assertHotWater("Автономное газовое отопление; горячая вода обеспечивается газовым котлом.", "gas")
        self.assertHotWater("An independent gas boiler provides heating and hot water.", "gas")
        self.assertHotWater("Vannas istabā gāzes kolonka.", "gas")

    def test_boiler_heated_water(self) -> None:
        self.assertHotWater("Karstais ūdens no boilera, iebūvētas mēbeles un tehnika.", "boiler")
        self.assertHotWater("Горячая вода от электрического бойлера.", "boiler")
        self.assertHotWater("A boiler is installed for hot water.", "boiler")
        self.assertHotWater("Siltais ūdens ziemā ar apkuri, vasarā ar boileri.", "boiler")
        self.assertHotWater("Dzīvoklī ir gāzes apkures katls un ūdens sildīšanas boilers.", "boiler")

    def test_meters_pipes_and_a_missing_boiler_say_nothing_about_the_source(self) -> None:
        self.assertHotWater("Uzstādīti siltuma, aukstā un karstā ūdens patēriņa skaitītāji.", "unknown")
        self.assertHotWater("Nomainīti aukstā un karstā ūdens stāvvadi.", "unknown")
        self.assertHotWater("Central heating is a big advantage as there is no boiler in the bathroom.", "unknown")
        self.assertHotWater("Jauns gāzes katls, siltas grīdas.", "unknown")
        self.assertHotWater("The apartment has its own gas boiler, adjustable radiators.", "unknown")
        self.assertHotWater("The building has an autonomous gas heating boiler.", "unknown")

    def test_a_gas_boiler_heats_the_water_only_when_the_listing_says_so_next_to_it(self) -> None:
        self.assertHotWater(
            "Mājai ir gāzes apkures katls, savukārt siltais ūdens dzīvoklī tiek nodrošināts ar boileri.", "boiler"
        )
        self.assertHotWater(
            "Centralizēta gāzes apkure (ēkai pagrabā ir savs gāzes katls), karstais ūdens tiek nodrošināts, "
            "izmantojot centralizēto gāzes katlu.",
            "central",
        )


class ShareFactTests(TestCase):
    def test_sale_as_a_share_is_detected(self) -> None:
        for text in (
            "Svarīgi: īpašums tiek pārdots kā domājamās daļas, bankas finansējums nav pieejams.",
            "Mājā ir 3 dzīvokļi. Dzīvoklis pārdodas kā domājamā daļa.",
            "Dzīvoklis reģistrēts kā domājamā daļa no nekustamā īpašuma.",
            "Важно: объект продаётся как идеальные доли.",
        ):
            fact = sells_share(_flat(text))
            self.assertEqual(fact.value, "yes", text)
            self.assertTrue(fact.evidence)

    def test_the_ordinary_land_share_of_a_flat_is_not_a_share_sale(self) -> None:
        for text in (
            "Zemes domājamās daļas īpašumā.",
            "Dzīvoklis tiek pārdots kopā ar zemes domājamo daļu.",
            "В собственность входит соответствующая доля земельного участка.",
        ):
            self.assertEqual(sells_share(_flat(text)).value, "no", text)


class LandFactTests(TestCase):
    def assertLand(self, description: str, value: str) -> None:
        fact = land_fact(_flat(description))
        self.assertEqual(fact.value, value, description)
        self.assertEqual(bool(fact.evidence), value != "unknown", description)

    def test_owned_land_in_latvian_and_russian(self) -> None:
        for text in (
            "Zeme ir īpašumā.",
            "Zeme zem mājas ir dzīvokļu īpašnieku īpašumā.",
            "Zemes domājamās daļas īpašumā.",
            "Zemes īpašumtiesības zem mājas.",
            "Земля под домом находится в собственности.",
        ):
            self.assertLand(text, "owned")

    def test_a_negated_lease_is_owned_land(self) -> None:
        for text in (
            "Zeme ir īpašumā (nav jāmaksā zemes noma).",
            "Zeme ir īpašumā - bez papildus nomas maksas par zemi.",
            "Zeme īpašumā – jums nebūs jāmaksā nomas maksa.",
            "Zeme ir īpašumā, nevis nomā.",
            "Земля в собственности – никакой аренды земли.",
            "Земля в собственности – не надо платить аренду за землю.",
            "Земля в собственности, не в аренде.",
        ):
            self.assertLand(text, "owned")

    def test_leased_land_in_latvian_and_russian(self) -> None:
        for text in (
            "Zeme zem mājas ir nomā.",
            "Zemes nomas maksa – 2,51 EUR/mēnesī.",
            "Zeme nav īpašumā.",
            "Zeme nav īpašumā – zemes lietošanas maksa 5,05 EUR mēnesī.",
            "Zeme īpašumā, neliela daļa nomā par pāris EUR gadā.",
            "Zeme daļēji īpašumā.",
            "Denacionalizēta māja.",
            "Земля под домом в аренде.",
            "Земля не в собственности.",
        ):
            self.assertLand(text, "leased")

    def test_words_that_only_look_like_a_lease(self) -> None:
        for text in (
            "Mājai nomainīti stāvvadi, zeme zem mājas ir īpašumā.",
            "Nin par šo gadu nomaksāts, zeme īpašumā.",
            "Zeme īpašumā, dzīvoklis šobrīd iznomāts.",
        ):
            self.assertLand(text, "owned")
        self.assertLand("Mājai nomainīti logi, zemi komunālie maksājumi.", "unknown")
        self.assertLand("Dzīvoklis atrodas zem jumta, īpašumā ir arī noliktava.", "unknown")
