"""Starter trivia questions (admin can edit, add, deactivate). Review them before launch."""
import json

from . import db

# (question, [options...], index_of_correct_option)
QUESTIONS = [
    ("Which country won the first FIFA World Cup in 1930?", ["Brazil", "Uruguay", "Argentina", "Italy"], 1),
    ("Who scored the 'Hand of God' goal at the 1986 World Cup?", ["Pelé", "Diego Maradona", "Zinedine Zidane", "Johan Cruyff"], 1),
    ("Which club has won the most European Cup / Champions League titles?", ["AC Milan", "Liverpool", "Real Madrid", "Bayern Munich"], 2),
    ("Which country hosted the 2022 FIFA World Cup?", ["Russia", "Qatar", "Brazil", "Japan"], 1),
    ("Which country won the 2022 FIFA World Cup?", ["France", "Croatia", "Argentina", "Morocco"], 2),
    ("Who has won the most Ballon d'Or awards?", ["Cristiano Ronaldo", "Lionel Messi", "Michel Platini", "Johan Cruyff"], 1),
    ("Which English club is nicknamed 'The Gunners'?", ["Chelsea", "Arsenal", "Tottenham", "West Ham"], 1),
    ("What is the name of Chelsea's home stadium?", ["Stamford Bridge", "Anfield", "Old Trafford", "Craven Cottage"], 0),
    ("What is the name of Arsenal's home stadium?", ["Emirates Stadium", "Goodison Park", "Villa Park", "Etihad Stadium"], 0),
    ("Which country won UEFA Euro 2016?", ["France", "Portugal", "Germany", "Wales"], 1),
    ("Who won the Golden Boot at the 2018 World Cup?", ["Harry Kane", "Antoine Griezmann", "Romelu Lukaku", "Kylian Mbappé"], 0),
    ("Which country has won the most Africa Cup of Nations titles?", ["Cameroon", "Nigeria", "Egypt", "Ghana"], 2),
    ("Which club won the 2012 Champions League final against Bayern Munich on penalties?", ["Chelsea", "Barcelona", "Inter Milan", "Manchester United"], 0),
    ("Who is the Premier League's all-time leading goalscorer?", ["Wayne Rooney", "Harry Kane", "Alan Shearer", "Thierry Henry"], 2),
    ("Which club won the Premier League in 2015-16 as huge outsiders?", ["Leicester City", "Tottenham", "Everton", "West Brom"], 0),
    ("Which team went the whole 2003-04 Premier League season unbeaten?", ["Manchester United", "Chelsea", "Arsenal", "Liverpool"], 2),
    ("Which country won the 2014 FIFA World Cup?", ["Brazil", "Germany", "Netherlands", "Argentina"], 1),
    ("Which Brazilian legend is known as 'O Rei' (The King)?", ["Ronaldo", "Pelé", "Romário", "Zico"], 1),
    ("Which goalkeeper won the Golden Glove at the 2022 World Cup?", ["Hugo Lloris", "Emiliano Martínez", "Yassine Bounou", "Dominik Livaković"], 1),
    ("In which year did Nigeria win Olympic football gold?", ["1992", "1996", "2000", "2004"], 1),
    ("How many minutes are played in regular time in a football match?", ["80", "90", "100", "120"], 1),
    ("Which club has won the most English top-flight league titles?", ["Liverpool", "Arsenal", "Manchester United", "Manchester City"], 2),
    ("Who scored the winning goal in the 2014 World Cup final?", ["Mario Götze", "Thomas Müller", "Lionel Messi", "Miroslav Klose"], 0),
    ("Which country has won the most men's FIFA World Cups?", ["Germany", "Italy", "Brazil", "Argentina"], 2),
    ("Which African country became the first to reach a World Cup semi-final in 2022?", ["Senegal", "Morocco", "Ghana", "Cameroon"], 1),
    ("Who scored a hat-trick in the 2022 World Cup final?", ["Lionel Messi", "Kylian Mbappé", "Julián Álvarez", "Olivier Giroud"], 1),
    ("Which colour card means a player is sent off?", ["Yellow", "Red", "Blue", "Green"], 1),
    ("Which country won UEFA Euro 2020 (played in 2021)?", ["England", "Italy", "Spain", "Belgium"], 1),
    ("Which country won the 2018 FIFA World Cup?", ["Croatia", "France", "Belgium", "Brazil"], 1),
    ("Who managed Arsenal's 'Invincibles' of 2003-04?", ["Arsène Wenger", "Alex Ferguson", "José Mourinho", "Unai Emery"], 0),
    ("Who is Chelsea's all-time leading goalscorer?", ["Didier Drogba", "Frank Lampard", "Eden Hazard", "Gianfranco Zola"], 1),
    ("Which club were the first Premier League champions in 1992-93?", ["Blackburn Rovers", "Manchester United", "Arsenal", "Aston Villa"], 1),
    ("Which country hosted the 2010 FIFA World Cup?", ["Nigeria", "Egypt", "South Africa", "Morocco"], 2),
    ("In which year did Nigeria win its first Africa Cup of Nations?", ["1974", "1980", "1994", "2013"], 1),
    ("Who is nicknamed 'CR7'?", ["Cristiano Ronaldo", "Ronaldinho", "Roberto Carlos", "Rivaldo"], 0),
]


def seed_questions_if_empty():
    if db.one("SELECT COUNT(*) n FROM questions")["n"]:
        return 0
    with db.tx() as c:
        for text, opts, correct in QUESTIONS:
            c.execute("INSERT INTO questions(text,options,correct_index) VALUES(?,?,?)",
                      (text, json.dumps(opts), correct))
    return len(QUESTIONS)
