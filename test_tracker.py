"""Matching rules for YouTube agent names. No network, no invented view counts."""
import unittest

from tracker import yt_video_matches

# The week of 15–21 Nov 2026 counted this film because the channel name is in the title.
# The description is the real one, shortened: a promo code and a URL, not the coding agent.
THAI_TITLE = '"ไม่กลัวก็ไม่ตาย" พาเปิดเรื่องสยองในหนังสั้นสองชุด Remain Calm และ Zygote | The Codex'
THAI_DESC = (
    "พาเปิดเรื่องราวของ 2 หนังสั้นสุดสยอง Remain Calm และ Zygote\n"
    "อย่าลืมใช้โค้ดของทางช่อง ลดเพิ่มอีก 100 บาท https://ophtus.com/codex\n"
    "หรือทักแอดมิน Line: @ophtus แจ้งโค้ด Codex\n"
    "Contact For Work : themoonlightcodex@gmail.com\n"
    "The Codex"
)


class YoutubeAgentMatch(unittest.TestCase):
    def test_thai_horror_film_from_the_codex_channel_does_not_count(self):
        self.assertFalse(yt_video_matches("Codex", THAI_TITLE, THAI_DESC, "The Codex"))

    def test_other_codex_namesakes_do_not_count(self):
        cases = [
            ("ลำนานบทเพลงแห่ง\"นาร์เนีย\" กำเนิดโลกของสิงโตและความชั่วร้ายในชุดขาว | The Codex", "", "The Codex"),
            ("RAS কী? | Law of Attraction | Divine Codex |", "A film about the mind.", "Divine Codex"),
            ("Reality Fracture vs. Strixhaven Codex — Which MTG Bundle Is Worth It?", "Magic: The Gathering bundle review.", "Zwyn's MtG"),
            ("One of the Earliest Greek Manuscripts of 1 Enoch: Codex Panopolitanus", "A Greek manuscript of 1 Enoch.", "Greg Lanier"),
            ("Codex Seraphinianus, raccontato dal suo autore", "An illustrated encyclopedia of an imaginary world.", "PDR"),
            ("ASTELLIA 2: COMPLETE CODEX GUIDE – BEST STATS, ITEM FARMING & ENHANCEMENT TIPS!!", "Item Codex and Collection Codex for a game.", "platyy"),
            ("Called a Reject, He Awakened the Codex That Lets Him Devour Races for Their Powers", "Anime highlight.", "ReelQuest"),
            ("DIA DECISIVO | DÓLAR, BITCOIN E MERCADOS GLOBAIS | CODEX MONEY", "Plano anual. Opere cripto.", "Prof. Walter Silva"),
            ("ANTRÓPIC, DÓLAR, BITCOIN E MERCADOS GLOBAIS EM QUEDA| CODEX MONEY", "Mercados em queda.", "Prof. Walter Silva"),
            ("(Creepypasta) Jurassic Boy Bootleg Game (by Alex Codex)", "A lost-episode story by Alex Codex.", "The Formidable Robot"),
            ("NIGHTMARE LEGION CODEX Made EASY! | Watcher of Realms", "A game boss guide.", "Infernum Gaming"),
            ("Codex of", "The channel posts horror lore.", "Programming Weekly"),  # the channel name is not context
        ]
        for title, description, channel in cases:
            self.assertFalse(yt_video_matches("Codex", title, description, channel), title)

    def test_warhammer_codex_does_not_count_even_with_openai_in_the_description(self):
        self.assertFalse(yt_video_matches(
            "Codex", "Codex review", "A Warhammer 40k army list, and someone says OpenAI.", "Warhammer TV"))

    def test_codex_coding_agent_videos_count(self):
        cases = [
            ("OpenAI Codex Full Course: Beginner to Advanced", "", "Shawn's AI Builds"),
            ("Codex vs Claude Code: Let’s Settle the Debate", "", "Hostinger Academy"),
            ("Codex с нуля — быстрый курс за 10 минут", "Курс по OpenAI Codex: установка и первые задачи.", "Владилен Минин"),
            ("CODEX #4 | Instalación y primeros pasos", "Instalamos Codex CLI y abrimos el primer proyecto.", "pildorasinformaticas"),
            ("【神回】無料AI編集ツール【Codex / ClaudeCode】", "", "AI動画編集"),
            ("Gentle Shell vs Codex: cuánto ahorrás realmente en tareas de código", "", "Gentleman Programming Vods"),
            ("New Ways of Working with Codex — Dominik Kundel & Charlie Guo, OpenAI", "", "AI Engineer"),
            ("OpenAI Codex review | The Codex", "The channel name is branding. The video is about the agent.", "The Codex"),
        ]
        for title, description, channel in cases:
            self.assertTrue(yt_video_matches("Codex", title, description, channel), title)

    def test_codex_title_alone_without_agent_context_does_not_count(self):
        self.assertFalse(yt_video_matches("Codex", "Codex с нуля — быстрый курс за 10 минут", "", "Владилен Минин"))

    def test_chatgpt_work_is_not_how_chatgpt_works(self):
        self.assertFalse(yt_video_matches("ChatGPT Work", "How ChatGPT Works: What Happens After You Press Send?", "", "Know It"))
        self.assertFalse(yt_video_matches(
            "ChatGPT Work", "How Do AI Systems Like ChatGPT Work? A Clear Explanation for Everyone!", "", "dotnet"))
        self.assertTrue(yt_video_matches(
            "ChatGPT Work", "How to Use ChatGPT Work for Finance: Beginner to Pro Tutorial (+ Codex)", "", "Ani Björkström"))
        self.assertTrue(yt_video_matches(
            "ChatGPT Work", "ChatGPT Work: Local Access, Plugin Skills & Office Integrations", "", "Snob Bots"))

    def test_let_grok_build_this_is_not_the_agent(self):
        title = "Want 2–3 More Buyers a Month? Let Grok Build This"
        description = (
            "You connect GoHighLevel to Grok, type one prompt, and Grok builds the whole thing. "
            "Step 3: watch Grok build the form, the 3 pages and the reminder workflow."
        )
        self.assertFalse(yt_video_matches("Grok Build", title, description, "Tristan Horn"))
        self.assertTrue(yt_video_matches("Grok Build", "Grok Build leaks apps and Outlook goes dark worldwide", "", "IBM Technology"))
        self.assertTrue(yt_video_matches("Grok Build", "Grok Build for Non-Coders: What It Can Do Locally", "", "APLittlewolf"))

    def test_other_agent_names_still_match_ordinary_titles(self):
        self.assertTrue(yt_video_matches("Claude Code", "Stop Paying for Claude Code (Use it for FREE!)", "", "Ishan Sharma"))
        self.assertTrue(yt_video_matches("Grok Bot", "The Smartest Way to Manage Your Grok Bots", "", "Van Did This!"))
        self.assertTrue(yt_video_matches("Grok Bot", "Grok Bot 101", "", "Grok"))
        self.assertTrue(yt_video_matches("Muse Code", "Building with Muse Code on Windows", "", "Microsoft Events"))


if __name__ == "__main__":
    unittest.main()
