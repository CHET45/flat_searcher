from unittest import TestCase

from flat_searcher.shortlist.facts import (
    LAYOUT_RANK,
    heating_fact,
    land_fact,
    layout_fact,
    sells_share,
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

    def test_another_heating_system_wins_over_a_stove(self) -> None:
        self.assertHeating("Krāsns apkure vai centrālā apkure pēc izvēles.", "other")
        self.assertHeating("Individuālā gāzes apkure.", "other")

    def test_kitchen_appliances_are_not_stoves(self) -> None:
        self.assertHeating("Cepeškrāsns, plīts virsma, mikroviļņu krāsns.", "unknown")
        self.assertHeating("Микроволновая печь и духовка.", "unknown")

    def test_a_stove_object_is_only_a_mention(self) -> None:
        self.assertHeating("Saglabājusies podiņu krāsns un augsti griesti.", "mention")
        self.assertHeating("Guļamistabā ir arī čuguna krāsniņa.", "mention")

    def test_a_removed_stove_is_not_stove_heating(self) -> None:
        self.assertHeating("Bez apkures. Ir bijusi krāsns apkure, kas ir demontēta.", "mention")


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
