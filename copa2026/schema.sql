-- ============================================================
-- Copa do Mundo 2026 — Supabase Schema
-- Execute no SQL Editor do Supabase
-- ============================================================

-- TEAMS
CREATE TABLE IF NOT EXISTS teams (
    id              SERIAL PRIMARY KEY,
    external_id     INTEGER UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    short_name      TEXT,
    tla             VARCHAR(3),         -- ex: BRA, ARG, FRA
    country         TEXT,
    crest_url       TEXT,
    group_name      TEXT,               -- A, B, C...
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- MATCHES
CREATE TABLE IF NOT EXISTS matches (
    id              SERIAL PRIMARY KEY,
    external_id     INTEGER UNIQUE NOT NULL,
    utc_date        TIMESTAMPTZ,
    status          TEXT,               -- TIMED, IN_PLAY, FINISHED, POSTPONED
    stage           TEXT,               -- GROUP_STAGE, ROUND_OF_16, QUARTER_FINAL, etc.
    group_name      TEXT,
    home_team_id    INTEGER REFERENCES teams(external_id),
    away_team_id    INTEGER REFERENCES teams(external_id),
    home_team_name  TEXT,
    away_team_name  TEXT,
    home_score      INTEGER,
    away_score      INTEGER,
    home_score_ht   INTEGER,            -- half-time
    away_score_ht   INTEGER,
    winner          TEXT,               -- HOME_TEAM, AWAY_TEAM, DRAW
    matchday        INTEGER,
    venue           TEXT,
    last_updated    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- GOALS (eventos de gol por partida)
CREATE TABLE IF NOT EXISTS goals (
    id              SERIAL PRIMARY KEY,
    match_id        INTEGER REFERENCES matches(external_id),
    minute          INTEGER,
    extra_time      INTEGER,
    type            TEXT,               -- REGULAR, OWN_GOAL, PENALTY
    team_id         INTEGER REFERENCES teams(external_id),
    team_name       TEXT,
    scorer_name     TEXT,
    scorer_id       INTEGER,
    assist_name     TEXT,
    assist_id       INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- BOOKINGS (cartões)
CREATE TABLE IF NOT EXISTS bookings (
    id              SERIAL PRIMARY KEY,
    match_id        INTEGER REFERENCES matches(external_id),
    minute          INTEGER,
    type            TEXT,               -- YELLOW_CARD, RED_CARD, YELLOW_RED_CARD
    team_id         INTEGER REFERENCES teams(external_id),
    team_name       TEXT,
    player_name     TEXT,
    player_id       INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- TOP SCORERS
CREATE TABLE IF NOT EXISTS top_scorers (
    id              SERIAL PRIMARY KEY,
    player_id       INTEGER,
    player_name     TEXT NOT NULL,
    nationality     TEXT,
    team_id         INTEGER,
    team_name       TEXT,
    goals           INTEGER DEFAULT 0,
    assists         INTEGER DEFAULT 0,
    penalties       INTEGER DEFAULT 0,
    played_matches  INTEGER DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id)
);

-- INGEST LOG (controle de execuções)
CREATE TABLE IF NOT EXISTS ingest_log (
    id              SERIAL PRIMARY KEY,
    source          TEXT NOT NULL,      -- 'matches', 'scorers', 'standings'
    status          TEXT NOT NULL,      -- 'success', 'error'
    records_upserted INTEGER DEFAULT 0,
    error_message   TEXT,
    executed_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- VIEWS ÚTEIS
-- ============================================================

-- Placar completo com nomes dos times
CREATE OR REPLACE VIEW v_matches_full AS
SELECT
    m.id,
    m.external_id,
    m.utc_date AT TIME ZONE 'America/Sao_Paulo' AS date_brazil,
    m.status,
    m.stage,
    m.group_name,
    m.matchday,
    m.home_team_name,
    m.away_team_name,
    m.home_score,
    m.away_score,
    m.home_score_ht,
    m.away_score_ht,
    m.winner,
    m.venue,
    ht.tla AS home_tla,
    at.tla AS away_tla,
    ht.crest_url AS home_crest,
    at.crest_url AS away_crest
FROM matches m
LEFT JOIN teams ht ON ht.external_id = m.home_team_id
LEFT JOIN teams at ON at.external_id = m.away_team_id
ORDER BY m.utc_date;

-- Artilharia com time
CREATE OR REPLACE VIEW v_top_scorers_full AS
SELECT
    rank() OVER (ORDER BY goals DESC, assists DESC) AS position,
    player_name,
    team_name,
    nationality,
    goals,
    assists,
    penalties,
    played_matches,
    ROUND(goals::NUMERIC / NULLIF(played_matches, 0), 2) AS goals_per_game
FROM top_scorers
ORDER BY goals DESC, assists DESC;

-- Estatísticas por time (gols marcados, sofridos, cartões)
CREATE OR REPLACE VIEW v_team_stats AS
SELECT
    t.name AS team,
    t.tla,
    t.group_name,
    COUNT(DISTINCT m.external_id) AS matches_played,
    SUM(CASE WHEN m.home_team_id = t.external_id THEN m.home_score ELSE 0 END +
        CASE WHEN m.away_team_id = t.external_id THEN m.away_score ELSE 0 END) AS goals_scored,
    SUM(CASE WHEN m.home_team_id = t.external_id THEN m.away_score ELSE 0 END +
        CASE WHEN m.away_team_id = t.external_id THEN m.home_score ELSE 0 END) AS goals_conceded,
    COUNT(CASE WHEN b.type = 'YELLOW_CARD' THEN 1 END) AS yellow_cards,
    COUNT(CASE WHEN b.type = 'RED_CARD' THEN 1 END) AS red_cards
FROM teams t
LEFT JOIN matches m
    ON (m.home_team_id = t.external_id OR m.away_team_id = t.external_id)
    AND m.status = 'FINISHED'
LEFT JOIN bookings b ON b.team_id = t.external_id
GROUP BY t.name, t.tla, t.group_name
ORDER BY goals_scored DESC;

-- Gols por faixa de minuto (curiosidade!)
CREATE OR REPLACE VIEW v_goals_by_minute_range AS
SELECT
    CASE
        WHEN minute BETWEEN 1  AND 15  THEN '01-15'
        WHEN minute BETWEEN 16 AND 30  THEN '16-30'
        WHEN minute BETWEEN 31 AND 45  THEN '31-45'
        WHEN minute BETWEEN 46 AND 60  THEN '46-60'
        WHEN minute BETWEEN 61 AND 75  THEN '61-75'
        WHEN minute BETWEEN 76 AND 90  THEN '76-90'
        ELSE '90+'
    END AS minute_range,
    COUNT(*) AS total_goals
FROM goals
WHERE type = 'REGULAR'
GROUP BY minute_range
ORDER BY minute_range;

-- ============================================================
-- ROW LEVEL SECURITY (leitura pública, escrita só via service key)
-- ============================================================

ALTER TABLE teams          ENABLE ROW LEVEL SECURITY;
ALTER TABLE matches         ENABLE ROW LEVEL SECURITY;
ALTER TABLE goals           ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings        ENABLE ROW LEVEL SECURITY;
ALTER TABLE top_scorers     ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_log      ENABLE ROW LEVEL SECURITY;

-- Leitura pública (para dashboard)
CREATE POLICY "public read teams"       ON teams       FOR SELECT USING (true);
CREATE POLICY "public read matches"     ON matches     FOR SELECT USING (true);
CREATE POLICY "public read goals"       ON goals       FOR SELECT USING (true);
CREATE POLICY "public read bookings"    ON bookings    FOR SELECT USING (true);
CREATE POLICY "public read scorers"     ON top_scorers FOR SELECT USING (true);
