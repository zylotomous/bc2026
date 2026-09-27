// UNSW Battlecode dragon bot -- strategy layer.
//
// Every dragon runs its own copy of this program. Each turn it scores every
// legal action (single steps, short sprints, a split) with a one-ply model of
// what the dragons around it can do before its next turn, and plays the best.
// Team behaviour emerges from shared deterministic rules: split early into a
// swarm that spreads out over the pearls, trade heads when the trade favours
// us, and consolidate into one long "king" dragon near the end.
//
// Every tunable is a P_* define with the tested default, so local experiments
// can rebuild with -DP_NAME=value (see tools/arena.py and tools/outcome.py).

#include "engine.hpp"

#ifndef P_NOISE
#define P_NOISE 0.0001
#endif

// ---------------------------------------------------------------- params
#ifndef P_END_START
#define P_END_START 400
#endif
#ifndef P_UNIT_VALUE
#define P_UNIT_VALUE 1.5
#endif
#ifndef P_AGGRESSION
#define P_AGGRESSION 1.5
#endif
#ifndef P_SPLIT_BONUS
#define P_SPLIT_BONUS 2.0
#endif
#ifndef P_POT_W
#define P_POT_W 1.0
#endif
#ifndef P_CROWD_W
#define P_CROWD_W 0.12
#endif
#ifndef P_LAMBDA
#define P_LAMBDA 0.85
#endif
#ifndef P_FAR_LAMBDA
#define P_FAR_LAMBDA 0.96
#endif
#ifndef P_FAR_W
#define P_FAR_W 0.0
#endif

static const int END_START = P_END_START;
static const double UNIT_VALUE = P_UNIT_VALUE;
static const double AGGRESSION = P_AGGRESSION;
#ifndef P_AGGR_BAL
#define P_AGGR_BAL 1.5
#endif
static const double AGGR_BAL = P_AGGR_BAL;
static double aggrNow = P_AGGRESSION;
static const double SPLIT_BONUS = P_SPLIT_BONUS;
static const double POT_W = P_POT_W;
static const double CROWD_W = P_CROWD_W;
#ifndef P_PORTAL_W
#define P_PORTAL_W 0.0
#endif
static const double PORTAL_W = P_PORTAL_W;
#ifndef P_PORTAL_RISK
#define P_PORTAL_RISK 0.4
#endif
static const double PORTAL_RISK = P_PORTAL_RISK;
#ifndef P_PROBE_CLEAR_RISK
#define P_PROBE_CLEAR_RISK 0.02
#endif
static const double PROBE_CLEAR_RISK = P_PROBE_CLEAR_RISK;
#ifndef P_PORTAL_WRONGWAY
#define P_PORTAL_WRONGWAY 0.0
#endif
static const double PORTAL_WRONGWAY = P_PORTAL_WRONGWAY;
#ifndef P_EVEN_TRADE_EARLY
#define P_EVEN_TRADE_EARLY 0.0
#endif
#ifndef P_EARLY_ROUNDS
#define P_EARLY_ROUNDS 80
#endif
static const double EVEN_TRADE_EARLY = P_EVEN_TRADE_EARLY;
static const int EARLY_ROUNDS = P_EARLY_ROUNDS;
#ifndef P_TERR_W
#define P_TERR_W 0.0
#endif
#ifndef P_CONTEND_ALL
#define P_CONTEND_ALL 1
#endif
static const double TERR_W = P_TERR_W;
#ifndef P_KING_TERR_W
#define P_KING_TERR_W 0.9
#endif
static const double KING_TERR_W = P_KING_TERR_W;
#ifndef P_RAM_REL
#define P_RAM_REL 0
#endif
static const bool RAM_REL = P_RAM_REL;
static const bool CONTEND_ALL = P_CONTEND_ALL;
#ifndef P_HUNT_ON
#define P_HUNT_ON 1
#endif
static const bool HUNT_ON = P_HUNT_ON;
#ifndef P_EXPOSE_ALL
#define P_EXPOSE_ALL 1
#endif
static const bool EXPOSE_ALL = P_EXPOSE_ALL;
#ifndef P_USE_DENSITY
#define P_USE_DENSITY 1
#endif
#ifndef P_DENS_MUL
#define P_DENS_MUL 1.5
#endif
static const bool USE_DENSITY = P_USE_DENSITY;
static const double DENS_MUL = P_DENS_MUL;
#ifndef P_KING_START
#define P_KING_START 60
#endif
#ifndef P_NO_KING_SPLIT
#define P_NO_KING_SPLIT 1
#endif
#ifndef P_KING_POT_MUL
#define P_KING_POT_MUL 1.5
#endif
#ifndef P_KING_PRIORITY
#define P_KING_PRIORITY 1
#endif
static const int KING_START = P_KING_START;
static const bool NO_KING_SPLIT = P_NO_KING_SPLIT;
static const double KING_POT_MUL = P_KING_POT_MUL;
static const bool KING_PRIORITY = P_KING_PRIORITY;
#ifndef P_KING_VAL_MUL
#define P_KING_VAL_MUL 2.0
#endif
#ifndef P_KING_VAL_ADD
#define P_KING_VAL_ADD 10.0
#endif
static const double KING_VAL_MUL = P_KING_VAL_MUL;
static const double KING_VAL_ADD = P_KING_VAL_ADD;
#ifndef P_UNIT_TARGET
#define P_UNIT_TARGET 64
#endif
static const int UNIT_TARGET = P_UNIT_TARGET;
#ifndef P_SURV_ON
#define P_SURV_ON 0
#endif
#ifndef P_SURV_DEPTH
#define P_SURV_DEPTH 10
#endif
#ifndef P_SURV_NODE_LIMIT
#define P_SURV_NODE_LIMIT 6000
#endif
static const bool SURV_ON = P_SURV_ON;
static const int SURV_DEPTH = P_SURV_DEPTH;
static const int SURV_NODE_LIMIT = P_SURV_NODE_LIMIT;
#ifndef P_SURV_KING
#define P_SURV_KING 1
#endif
#ifndef P_SURV_KING_DEPTH
#define P_SURV_KING_DEPTH 14
#endif
static const bool SURV_KING = P_SURV_KING;
static const int SURV_KING_DEPTH = P_SURV_KING_DEPTH;
static const double LAMBDA = P_LAMBDA;
static const double FAR_LAMBDA = P_FAR_LAMBDA;
static const double FAR_W = P_FAR_W;
#ifndef P_POT_DEPTH
#define P_POT_DEPTH 90
#endif
static const int POT_DEPTH = P_POT_DEPTH;
// Immediate value of a pearl eaten this turn. The potential field credits a
// skipped pearl at ~lambda, as if I would surely come back for it; in a busy
// map I rarely do, so eating now must clearly beat walking past.
#ifndef P_EAT_W
#define P_EAT_W 3.0
#endif
static const double EAT_W = P_EAT_W;
#ifndef P_THR_P2
#define P_THR_P2 0.35
#endif
#ifndef P_THR_P3
#define P_THR_P3 0.15
#endif
#ifndef P_EVEN_EXPOSE
#define P_EVEN_EXPOSE 0.1
#endif
#ifndef P_LAST_UNIT_PEN
#define P_LAST_UNIT_PEN 5.0
#endif
#ifndef P_SPRINT_W
#define P_SPRINT_W 3.0
#endif
#ifndef P_THR_MIN
#define P_THR_MIN 0.5
#endif
static const double SPRINT_W = P_SPRINT_W, THR_MIN = P_THR_MIN;
static const double THR_P2 = P_THR_P2, THR_P3 = P_THR_P3, EVEN_EXPOSE = P_EVEN_EXPOSE, LAST_UNIT_PEN = P_LAST_UNIT_PEN;

// Work budget per turn, in judge points (== virtual nanoseconds).
static const long long SOFT_BUDGET = 45000000LL;
static long long turnStartNs = 0;
static inline long long spentNs() { return nowNs() - turnStartNs; }

// ---------------------------------------------------------------- per-turn fields
static int freeAtO[MAXT];     // others' bodies: earliest step a tile may be entered
static int freeAtAll[MAXT];   // others + my current body
static bool contested[MAXT];  // a tile some other head could step onto before my next turn
static int distMe[MAXT], distAlly[MAXT], distEnemy[MAXT];
static double tileVal[MAXT];
static double powLam[256];
static double powFar[256];
static double powKeep[256];
static double potCache[MAXT];
static int potStamp[MAXT], potGen = 1;

static int bfsQ[MAXT];
static int stampA[MAXT], stampGen = 1;
static int segIdx[MAXT];

static inline bool isEnemyTeam(int team) { return team >= 0 && team != myTeam; }

// Static dead ends: repeatedly prune tiles with at most one open neighbour.
// What gets pruned are corridors and pockets that lead nowhere -- fine for a
// short dragon (and a pearl mill), deadly for a long king.
static bool deadEnd[MAXT];
static int deadEndMapIndex = -99;
static void computeDeadEnds() {
    if (deadEndMapIndex == mapIndex) return;
    deadEndMapIndex = mapIndex;
    static int deg[MAXT];
    int qh = 0, qt = 0;
    for (int t = 0; t < T; t++) {
        deadEnd[t] = false;
        int dg = 0;
        for (int d = 0; d < 4; d++) {
            int v = stepTo[t][d];
            if (v >= 0 && v != t) dg++;
        }
        deg[t] = dg;
        if (dg <= 1) { deadEnd[t] = true; bfsQ[qt++] = t; }
    }
    while (qh < qt) {
        int u = bfsQ[qh++];
        for (int d = 0; d < 4; d++) {
            int v = stepTo[u][d];
            if (v < 0 || deadEnd[v]) continue;
            if (--deg[v] <= 1) { deadEnd[v] = true; bfsQ[qt++] = v; }
        }
    }
}

static void buildFreeAt() {
    for (int t = 0; t < T; t++) { freeAtO[t] = 0; contested[t] = false; }
    for (auto& dv : dragons) {
        if (dv.id == myId) continue;
        int L = dv.exact ? dv.len : dv.len + 3;
        if (dv.head >= 0) {
            for (size_t i = 0; i < dv.chain.size(); i++) {
                int f = L - (int)i + 1;
                if (f < 1) f = 1;
                if (f > freeAtO[dv.chain[i]]) freeAtO[dv.chain[i]] = f;
            }
            for (int d = 0; d < 4; d++) {
                int q = stepTo[dv.head][d];
                if (q >= 0) contested[q] = true;
            }
        }
    }
    for (size_t i = 0; i < parts.size(); i++) {
        if (parts[i].id == myId) continue;
        int t = partTile[i];
        if (freeAtO[t] == 0) freeAtO[t] = 6;
    }
    // Forced moves: a dragon with a single legal move will make it (lanes,
    // corridors). Follow such chains a few steps and block the tiles they
    // will fill, so nobody walks into a head-on in a one-wide lane.
    for (auto& dv : dragons) {
        if (dv.id == myId || dv.head < 0) continue;
        int L = dv.exact ? dv.len : dv.len + 2;
        int h = dv.head;
        int neck = dv.chain.size() > 1 ? dv.chain[1] : -1;
        for (int k = 1; k <= 4; k++) {
            int only = -1, nOpt = 0;
            for (int d = 0; d < 4; d++) {
                int v = stepTo[h][d];
                if (v < 0 || v == neck) continue;
                if (k < freeAtO[v]) continue;
                if (k == 1 && vis[v] && occ[v] >= 0) continue;
                bool mine = false;
                for (int b : myBody) if (b == v) { mine = true; break; }
                if (mine) continue;
                nOpt++;
                only = v;
            }
            if (nOpt != 1) break;
            int f = k + L;
            if (f > freeAtO[only]) freeAtO[only] = f;
            neck = h;
            h = only;
        }
    }
    for (int t = 0; t < T; t++) freeAtAll[t] = freeAtO[t];
    int L = (int)myBody.size();
    for (int i = 0; i < L; i++) {
        int f = myLen - i + 1;
        if (f > freeAtAll[myBody[i]]) freeAtAll[myBody[i]] = f;
    }
}

// Multi-source time-aware BFS over freeAtAll. Unreached tiles get INF.
static void bfsField(const int* srcs, int n, int* dist, int maxDepth) {
    for (int t = 0; t < T; t++) dist[t] = INF;
    int qh = 0, qt = 0;
    for (int i = 0; i < n; i++) {
        if (dist[srcs[i]] == 0) continue;
        dist[srcs[i]] = 0;
        bfsQ[qt++] = srcs[i];
    }
    while (qh < qt) {
        int u = bfsQ[qh++];
        int du = dist[u];
        if (du >= maxDepth) continue;
        for (int d = 0; d < 4; d++) {
            int v = stepTo[u][d];
            if (v < 0 || dist[v] != INF) continue;
            if (du + 1 < freeAtAll[v]) continue;
            dist[v] = du + 1;
            bfsQ[qt++] = v;
        }
    }
}

// Expected value of a pearl being collectable at tile t (before contention).
// Pearl value of a tile as a function of when I would get there. Each tile gets
// a small model this turn; valueAt(t, d) evaluates it for arrival in d turns.
enum TileModel : uint8_t { TM_NONE, TM_PEARL, TM_SPAWN_AT, TM_REMEMBERED, TM_UNKNOWN_CD, TM_NEVER_SEEN };
static uint8_t tmKind[MAXT];
static int tmA[MAXT];      // spawn round / age
static float tmMul[MAXT];  // contention multiplier

static void buildTileModel(int t) {
    tmKind[t] = TM_NONE;
    if (gmax[t] == 0) return;
    if (vis[t]) {
        if (pearlNow[t]) { tmKind[t] = TM_PEARL; return; }
        if (countdownNow[t] >= 0) { tmKind[t] = TM_SPAWN_AT; tmA[t] = roundNum + countdownNow[t]; }
        return;
    }
    if (lastSeen[t] >= 0) {
        if (pearlMem[t]) { tmKind[t] = TM_REMEMBERED; tmA[t] = roundNum - lastSeen[t]; return; }
        if (spawnAt[t] >= 0) { tmKind[t] = TM_SPAWN_AT; tmA[t] = spawnAt[t]; return; }
        tmKind[t] = TM_UNKNOWN_CD; tmA[t] = roundNum - lastSeen[t];
        return;
    }
    tmKind[t] = TM_NEVER_SEEN;
}

// Observed pearl density: the share of spawnable tiles in my window holding a
// pearl, averaged over turns. Tiles I have not seen lately are harvested by
// everyone else too, so their value is capped at what I actually observe.
static double densityEst = -1;
// Rate-relative density: pearls seen per unit of spawn rate. A tile that
// spawns five times faster than my surroundings is capped five times higher,
// so rich clusters I have not visited still look rich.
#ifndef P_DENS_REL
#define P_DENS_REL 0
#endif
static double densityRel = -1;
static double rateRaw[MAXT];
static int rateRawMap = -99;
static void computeRawRates() {
    if (rateRawMap == mapIndex) return;
    rateRawMap = mapIndex;
    for (int t = 0; t < T; t++) rateRaw[t] = gmax[t] > 0 ? 2.0 / (std::max<int>(gmin[t], 0) + gmax[t]) : 0.0;
}
static void updateDensity() {
    int n = 0, p = 0;
    double rs = 0;
    if (P_DENS_REL && mapKnown) computeRawRates();
    for (int i = 0; i < 49; i++) {
        int t = visList[i];
        if (gmax[t] == 0 || (occ[t] >= 0)) continue;
        n++;
        if (pearlNow[t]) p++;
        if (P_DENS_REL && mapKnown) rs += rateRaw[t];
    }
    if (n < 5) return;
    double obs = (double)p / n;
    if (densityEst < 0) densityEst = obs;
    else densityEst = 0.9 * densityEst + 0.1 * obs;
    if (P_DENS_REL && mapKnown && rs > 0) {
        double rel = p / rs;
        if (densityRel < 0) densityRel = rel;
        else densityRel = 0.9 * densityRel + 0.1 * rel;
    }
}
static inline double densCap(double v, int t) {
    if (!USE_DENSITY || densityEst < 0) return v;
    double cap;
    if (P_DENS_REL && mapKnown && densityRel >= 0) cap = std::min(1.0, DENS_MUL * densityRel * rateRaw[t]) + 0.02;
    else cap = DENS_MUL * densityEst + 0.02;
    return v < cap ? v : cap;
}

static inline double valueAt(int t, int d) {
    double v;
    switch (tmKind[t]) {
    case TM_PEARL: v = 1.0; break;
    case TM_SPAWN_AT: {
        int wait = tmA[t] - (roundNum + d);  // > 0: I would have to wait
        if (wait > 0) v = 0.8 * powLam[std::min(wait, 255)];
        else if (wait > -5) v = 0.8;
        else v = std::max(densCap(0.8, t), 0.8 * std::max(0.0, 1.0 + 0.03 * (wait + 5)));  // spawned a while ago: may be taken
        break;
    }
    case TM_REMEMBERED: v = 0.85 * powKeep[std::min(tmA[t] + d, 255)]; break;
    case TM_UNKNOWN_CD: {
        double mg = gmax[t] > 0 ? 0.5 * (gmin[t] + gmax[t]) : 150.0;
        v = densCap(0.45 * std::min(1.0, (tmA[t] + d) / mg), t);
        break;
    }
    case TM_NEVER_SEEN: {
        double lo = gmin[t] > 0 ? gmin[t] : 1, hi = gmax[t] > 0 ? gmax[t] : 300;
        double p = (roundNum + d - lo + 1) / (hi - lo + 1);
        v = densCap(0.6 * std::max(0.0, std::min(1.0, p)), t);
        break;
    }
    default: return 0;
    }
    return v * tmMul[t];
}

static bool kingVisibleNow = false;
static int kingTileNow = -1, kingIdNow = -1;
static void computeTileValues() {
    for (int t = 0; t < T; t++) {
        buildTileModel(t);
        double m = 1.0;
        bool meKing = KING_PRIORITY && kingIdNow == myId;
        if (tmKind[t] != TM_NONE && (CONTEND_ALL || vis[t])) {
            int dm = distMe[t];
            if (!meKing) {  // the king never yields a pearl to a teammate
                if (distAlly[t] < dm) m *= 0.25;
                else if (distAlly[t] == dm) m *= 0.6;
            }
            if (distEnemy[t] < dm) m *= 0.6;
        }
        if (KING_PRIORITY && kingVisibleNow && tmKind[t] != TM_NONE && myId != kingIdNow &&
            manhattan(t, kingTileNow) <= distMe[t] + 1)
            m *= 0.2;
        tmMul[t] = (float)m;
        tileVal[t] = valueAt(t, distMe[t] < INF ? distMe[t] : 0);
    }
}

// Discounted pearl mass reachable from tile src (excluding src itself).
static bool potAvoidDeadEnds = false;
// Best single target: the sum above has a weak, noisy gradient in busy maps;
// the most valuable nearby pearl gives a clear direction to walk in.
#ifndef P_BEST_W
#define P_BEST_W 2.0
#endif
#ifndef P_BEST_LAMBDA
#define P_BEST_LAMBDA 0.88
#endif
static const double BEST_W = P_BEST_W;
static double powBest[256];
static double potentialAt(int src) {
    if (potStamp[src] == potGen) return potCache[src];
    stampGen++;
    int qh = 0, qt = 0;
    bfsQ[qt++] = src;
    stampA[src] = stampGen;
    segIdx[src] = 0;  // reuse as distance
    double acc = 0, best = 0;
    while (qh < qt) {
        int u = bfsQ[qh++];
        int du = segIdx[u];
        if (du >= POT_DEPTH) continue;
        for (int d = 0; d < 4; d++) {
            int v = stepTo[u][d];
            if (v < 0 || stampA[v] == stampGen) continue;
            if (du + 1 < freeAtAll[v]) continue;
            if (potAvoidDeadEnds && deadEnd[v]) continue;
            stampA[v] = stampGen;
            segIdx[v] = du + 1;
            bfsQ[qt++] = v;
            if (tmKind[v] != TM_NONE) {
                double val = valueAt(v, du + 1);
                if (val > 0) {
                    acc += val * (powLam[du + 1] + FAR_W * powFar[du + 1]);
                    double b = val * powBest[du + 1];
                    if (b > best) best = b;
                }
            }
        }
    }
    potStamp[src] = potGen;
    potCache[src] = acc + BEST_W * best;
    return acc + BEST_W * best;
}

// Yield field: long-run pearl production around a tile. Every spawnable tile
// produces about 2/(min_gap+max_gap) pearls a round while it stays empty
// (a fountain with gaps 1..1 makes one every round). A tile counts fully when
// I would be the first head there and much less when someone else is closer,
// so dragons spread over the productive ground instead of piling up.
#ifndef P_YIELD_W
#define P_YIELD_W 0.0
#endif
#ifndef P_YIELD_LAMBDA
#define P_YIELD_LAMBDA 0.9
#endif
#ifndef P_YIELD_DEPTH
#define P_YIELD_DEPTH 30
#endif
#ifndef P_YIELD_SHARE
#define P_YIELD_SHARE 0.25
#endif
static const double YIELD_W = P_YIELD_W;
static const int YIELD_DEPTH = P_YIELD_DEPTH;
static const double YIELD_SHARE = P_YIELD_SHARE;
static double rateT[MAXT];
static double powY[256];
static double yCache[MAXT];
static int yStamp[MAXT], yGen = 1;
static int yDist[MAXT], yVis[MAXT], yVisGen = 1;
#ifndef P_YIELD_EXCESS
#define P_YIELD_EXCESS 1.0
#endif
static void computeRates() {
    // only production above the map's average counts: uniform maps have no field
    double sum = 0; int n = 0;
    for (int t = 0; t < T; t++) {
        rateT[t] = gmax[t] > 0 ? 2.0 / (std::max<int>(gmin[t], 0) + gmax[t]) : 0.0;
        if (gmax[t] > 0) { sum += rateT[t]; n++; }
    }
    double base = n ? P_YIELD_EXCESS * sum / n : 0.0;
    for (int t = 0; t < T; t++) rateT[t] = std::max(0.0, rateT[t] - base);
}
static double yieldAt(int src) {
    if (yStamp[src] == yGen) return yCache[src];
    yVisGen++;
    int qh = 0, qt = 0;
    bfsQ[qt++] = src;
    yVis[src] = yVisGen;
    yDist[src] = 0;
    double acc = 0;
    while (qh < qt) {
        int u = bfsQ[qh++];
        int du = yDist[u];
        double r = rateT[u];
        if (r > 0) {
            double sh = (du < distAlly[u] && du < distEnemy[u]) ? 1.0 : YIELD_SHARE;
            acc += r * sh * powY[du];
        }
        if (du >= YIELD_DEPTH) continue;
        for (int d = 0; d < 4; d++) {
            int v = stepTo[u][d];
            if (v < 0 || yVis[v] == yVisGen) continue;
            if (du + 1 < freeAtAll[v]) continue;
            yVis[v] = yVisGen;
            yDist[v] = du + 1;
            bfsQ[qt++] = v;
        }
    }
    yStamp[src] = yGen;
    yCache[src] = acc;
    return acc;
}

// Tiles reachable from the head of newBody (time-aware), up to maxCount.
// pessimistic: tiles other heads could take are closed for a few steps.
static int spaceCount(const std::vector<int>& body, int len, int maxCount, bool pessimistic,
                      const std::vector<int>* extra = nullptr) {
    stampGen++;
    int gen = stampGen;
    int L = (int)body.size();
    // mark my body with its segment index (need a separate stamp from the BFS)
    static int bodyStamp[MAXT], bodyGen = 1;
    static int extraFree[MAXT], extraGen[MAXT], exGen = 1;
    bodyGen++;
    exGen++;
    for (int i = 0; i < L; i++) { bodyStamp[body[i]] = bodyGen; segIdx[body[i]] = i; }
    if (extra) {
        int EL = (int)extra->size();
        for (int i = 0; i < EL; i++) { extraGen[(*extra)[i]] = exGen; extraFree[(*extra)[i]] = EL - i + 1; }
    }
    static int dist[MAXT];
    int qh = 0, qt = 0;
    int src = body[0];
    bfsQ[qt++] = src;
    stampA[src] = gen;
    dist[src] = 0;
    int count = 1;
    while (qh < qt) {
        int u = bfsQ[qh++];
        int du = dist[u];
        if (du >= 60) continue;
        for (int d = 0; d < 4; d++) {
            int v = stepTo[u][d];
            if (v < 0 || stampA[v] == gen) continue;
            int s = du + 1;
            int f = freeAtO[v];
            if (bodyStamp[v] == bodyGen) {
                int fi = len - segIdx[v] + 1;
                if (fi > f) f = fi;
            }
            if (extra && extraGen[v] == exGen && extraFree[v] > f) f = extraFree[v];
            if (s < f) continue;
            // Territory: s == 1 is my next turn, after every other dragon has
            // moved once, so a tile is mine only if I get there strictly before
            // any enemy head could (and no later than an ally).
            if (pessimistic && (s >= distEnemy[v] || s >= distAlly[v])) continue;
            stampA[v] = gen;
            dist[v] = s;
            bfsQ[qt++] = v;
            if (++count >= maxCount) return count;
        }
    }
    return count;
}

// ---------------------------------------------------------------- survival lookahead
// Depth-limited search over my own future moves: is there a way to stay alive
// for `depth` more turns? My body moves exactly (tail follows, no growth);
// other bodies recede on their schedule (freeAtO); enemy heads are assumed to
// spread one tile per turn; allies may take a tile next to their head on the
// first step. Returns the deepest survivable depth found (== depth: safe).
static int survOcc[MAXT];
static int survRing[256];
static int survNodes = 0;
static int survBest = 0;

static bool survDfs(int headIdx, int len, int step, int depth, int neck) {
    if (step > survBest) survBest = step;
    if (step >= depth) return true;
    if (++survNodes > SURV_NODE_LIMIT) return false;
    int head = survRing[headIdx & 255];
    int s = step + 1;
    for (int d = 0; d < 4; d++) {
        int v = stepTo[head][d];
        if (v < 0 || v == neck) continue;
        if (survOcc[v] > 0) continue;
        if (s < freeAtO[v]) continue;
        if (distEnemy[v] <= s) continue;
        if (s == 1 && distAlly[v] <= 1) continue;
        // move: new head v, tail leaves
        int tailIdx = headIdx + len - 1;
        int tail = survRing[tailIdx & 255];
        survRing[(headIdx - 1) & 255] = v;
        survOcc[v]++;
        survOcc[tail]--;
        bool ok = survDfs(headIdx - 1, len, s, depth, head);
        survOcc[tail]++;
        survOcc[v]--;
        if (ok) return true;
        if (survNodes > SURV_NODE_LIMIT) return false;
    }
    return false;
}

static int survivalDepth(const std::vector<int>& body, int len, int depth) {
    int L = std::min((int)body.size(), 200);
    if (L == 0) return 0;
    for (int i = 0; i < L; i++) { survRing[(1000 + i) & 255] = body[i]; survOcc[body[i]]++; }
    survNodes = 0;
    survBest = 0;
    int neck = L > 1 ? body[1] : -1;
    survDfs(1000, L, 0, depth, neck);
    for (int i = 0; i < L; i++) survOcc[body[i]]--;
    (void)len;
    return survBest;
}

// ---------------------------------------------------------------- threat model
static int thrStamp[MAXT], thrGen = 1, thrDist[MAXT];

// Can the head at e step into `target` within `reach` steps before my next
// turn? Obstacles: visible bodies and my new body.
static bool canReach(int e, int target, int reach, const std::vector<int>& newBody);
// Steps the head at e needs to step into `target` (<= reach), or INF.
static int reachSteps(int e, int target, int reach, const std::vector<int>& newBody) {
    thrGen++;
    static int nbStamp[MAXT], nbGen = 1;
    nbGen++;
    for (int t : newBody) nbStamp[t] = nbGen;
    int qh = 0, qt = 0;
    bfsQ[qt++] = e;
    thrStamp[e] = thrGen;
    thrDist[e] = 0;
    while (qh < qt) {
        int u = bfsQ[qh++];
        if (thrDist[u] >= reach) continue;
        for (int d = 0; d < 4; d++) {
            int v = stepTo[u][d];
            if (v < 0 || thrStamp[v] == thrGen) continue;
            if (v == target) return thrDist[u] + 1;
            if (nbStamp[v] == nbGen) continue;
            if (vis[v] && occ[v] >= 0 && parts[occ[v]].id != myId) continue;
            thrStamp[v] = thrGen;
            thrDist[v] = thrDist[u] + 1;
            bfsQ[qt++] = v;
        }
    }
    return INF;
}
static bool canReach(int e, int target, int reach, const std::vector<int>& newBody) {
    return reachSteps(e, target, reach, newBody) <= reach;
}

// Distance (in steps, through portals) from tile src to the nearest tile I
// cannot see. Unseen tiles within a few steps may hide an attacker -- the
// vision window stops at portals, so a portal exit next to me is blind.
static int unseenDist(int src, const std::vector<int>& body, int maxD) {
    thrGen++;
    static int nbStamp[MAXT], nbGen = 1;
    nbGen++;
    for (int t : body) nbStamp[t] = nbGen;
    int qh = 0, qt = 0;
    bfsQ[qt++] = src;
    thrStamp[src] = thrGen;
    thrDist[src] = 0;
    while (qh < qt) {
        int u = bfsQ[qh++];
        if (thrDist[u] >= maxD) continue;
        for (int d = 0; d < 4; d++) {
            int v = stepTo[u][d];
            if (v < 0 || thrStamp[v] == thrGen) continue;
            if (!vis[v]) return thrDist[u] + 1;
            if (nbStamp[v] == nbGen) continue;
            if (occ[v] >= 0 && parts[occ[v]].id != myId) continue;
            thrStamp[v] = thrGen;
            thrDist[v] = thrDist[u] + 1;
            bfsQ[qt++] = v;
        }
    }
    return INF;
}

static int reachOf(const DragonView& dv) {
    int L = dv.exact ? dv.len : dv.len + 1;
    int r = L - 1;
    if (r < 1) r = 1;
    if (r > 6) r = 6;
    return r;
}

// ---------------------------------------------------------------- comms
// Sonar gossip. Every dragon relays the best facts it knows: which of our
// dragons is the king (the lowest living id), where it was and when, and the
// biggest enemy seen lately. A 16-bit keyed check rejects foreign or
// corrupted values. A dragon next to a portal sends a single probe ray
// through it instead (see readProbe).
//   bits  0..15 check   16..18 type   19..30 tile   31..38 len
//   bits 39..47 round   48..59 id     60..63 spare
static const uint64_t SECRET = 0xB5AD4ECEDA1CE2A9ULL;
enum MsgType { MSG_KING = 1, MSG_ENEMY = 2 };

struct KInfo {
    bool valid = false;
    int id = -1, tile = -1, len = 0, round = -1000;
    bool crowned = false;  // has taken the king role; beats any uncrowned claim
};
static KInfo bestKing, enemyBig;
static bool wasKing = false;
static const int KING_STALE = 25;
static const int ENEMY_STALE = 12;

static inline uint64_t mix64(uint64_t x) {
    x ^= x >> 33; x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return x;
}
static uint64_t encodeMsg(int type, const KInfo& k) {
    uint64_t p = 0;
    p |= (uint64_t)(type & 7) << 16;
    p |= (uint64_t)(k.tile & 0xFFF) << 19;
    p |= (uint64_t)(std::min(k.len, 255) & 0xFF) << 31;
    p |= (uint64_t)(k.round & 0x1FF) << 39;
    p |= (uint64_t)(k.id & 0xFFF) << 48;
    if (k.crowned) p |= 1ULL << 60;
    uint64_t check = mix64(p ^ SECRET ^ (uint64_t)myTeam) & 0xFFFF;
    return p | check;
}
static bool decodeMsg(uint64_t v, int& type, KInfo& k) {
    uint64_t p = v & ~0xFFFFULL;
    if ((mix64(p ^ SECRET ^ (uint64_t)myTeam) & 0xFFFF) != (v & 0xFFFF)) return false;
    type = (int)((v >> 16) & 7);
    k.valid = true;
    k.tile = (int)((v >> 19) & 0xFFF);
    k.len = (int)((v >> 31) & 0xFF);
    int r9 = (int)((v >> 39) & 0x1FF);
    // rounds are < 512: pick the latest round <= now with these low 9 bits
    int r = (roundNum & ~0x1FF) | r9;
    if (r > roundNum) r -= 512;
    k.round = r;
    k.id = (int)((v >> 48) & 0xFFF);
    k.crowned = (v >> 60) & 1;
    if (k.tile >= T || k.len < 1) return false;
    return type == MSG_KING || type == MSG_ENEMY;
}

static bool fresher(const KInfo& a, const KInfo& b, int stale) {
    // is a better than b? (enemy sightings: the biggest recent one)
    bool aOk = a.valid && roundNum - a.round <= stale;
    bool bOk = b.valid && roundNum - b.round <= stale;
    if (!aOk) return false;
    if (!bOk) return true;
    if (a.id == b.id) return a.round > b.round || (a.round == b.round && a.len > b.len);
    if (a.len != b.len) return a.len > b.len;
    return a.id < b.id;
}
// Our king is simply the living dragon of ours with the lowest id: everyone
// agrees on who it is without comparing lengths; gossip only says where.
// With P_KING_LONGEST the king is instead our longest dragon: the length
// race is what decides the game, and feeding a short dragon wastes the food.
// The crowned incumbent keeps the title unless a challenger is clearly longer.
#ifndef P_KING_LONGEST
#define P_KING_LONGEST 0
#endif
#ifndef P_KING_MARGIN
#define P_KING_MARGIN 4
#endif
static bool kingBetter(const KInfo& a, const KInfo& b) {
    bool aOk = a.valid && roundNum - a.round <= KING_STALE;
    bool bOk = b.valid && roundNum - b.round <= KING_STALE;
    if (!aOk) return false;
    if (!bOk) return true;
    if (P_KING_LONGEST && a.id != b.id) {
        int sa = a.len + (a.crowned ? P_KING_MARGIN : 0), sb = b.len + (b.crowned ? P_KING_MARGIN : 0);
        if (sa != sb) return sa > sb;
        return a.id < b.id;
    }
    if (a.id != b.id) return a.id < b.id;
    return a.round > b.round || (a.round == b.round && a.len > b.len);
}
static void offerKing(const KInfo& k) { if (kingBetter(k, bestKing)) bestKing = k; }
static void offerEnemy(const KInfo& k) { if (fresher(k, enemyBig, ENEMY_STALE)) enemyBig = k; }

static void processComms() {
    for (uint64_t v : sonarIn) {
        int type; KInfo k;
        if (!decodeMsg(v, type, k)) continue;
        if (type == MSG_KING) offerKing(k);
        else offerEnemy(k);
    }
    // what I see myself beats hearsay
    KInfo me; me.valid = true; me.id = myId; me.tile = myHeadTile; me.len = myLen; me.round = roundNum;
    me.crowned = wasKing;
    offerKing(me);
    for (auto& dv : dragons) {
        if (dv.id == myId || dv.head < 0) continue;
        KInfo k; k.valid = true; k.id = dv.id; k.tile = dv.head; k.len = dv.len; k.round = roundNum;
        if (dv.team == myTeam) {
            // seeing the crowned king refreshes it; seeing others only offers them
            if (bestKing.valid && bestKing.id == dv.id) { k.crowned = bestKing.crowned; k.len = std::max(k.len, bestKing.len); }
            offerKing(k);
        }
        else if (dv.len >= 6) offerEnemy(k);
    }
    // the king I knew of is gone if its tile is in view and it is not there
    if (bestKing.valid && bestKing.id != myId && vis[bestKing.tile] && roundNum - bestKing.round <= 1) {
        bool seen = false;
        for (auto& dv : dragons) if (dv.id == bestKing.id) seen = true;
        if (!seen) bestKing.valid = false;
    }
    if (enemyBig.valid && vis[enemyBig.tile] && roundNum - enemyBig.round <= 2) {
        bool seen = false;
        for (auto& dv : dragons) if (dv.id == enemyBig.id) seen = true;
        if (!seen) enemyBig.valid = false;
    }
}

// Portal probes. Vision stops at portals, but sonar goes through them, and the
// next turn's echo counts say what the ray stopped on. A dragon that ends its
// turn next to a portal sends a single ray through it; if nothing answers, the
// exit is clear to cross next turn.
static int probeTile = -1, probeDir = -1, probeRound = -10;
static bool probeKnown = false, probeClear = false;
static int nextHeadTile = -1, nextFacing = 0;

static void readProbe() {
    probeKnown = false;
    if (probeRound == roundNum - 1 && probeTile == myHeadTile) {
        probeKnown = true;
        probeClear = echoes[1] + echoes[2] + echoes[3] + echoes[4] == 0;
    }
}

static void emitSonar() {
    char buf[64];
    bool haveEnemy = enemyBig.valid && roundNum - enemyBig.round <= ENEMY_STALE;
    bool haveKing = bestKing.valid && roundNum - bestKing.round <= KING_STALE;
    int probe = -1;
    if (nextHeadTile >= 0) {
        for (int d = 0; d < 4 && probe < 0; d++) {
            if (d == ((nextFacing + 2) & 3)) continue;  // that ray would leave from my tail
            int et, side; edgeOf(nextHeadTile, d, et, side);
            if (edgeKind(et, side) == 2) probe = d;
        }
    }
    probeRound = -10;
    if (probe >= 0) { probeTile = nextHeadTile; probeDir = probe; probeRound = roundNum; }
    for (int d = 0; d < 4; d++) {
        if (probe >= 0 && d != probe) continue;
        uint64_t v;
        bool enemyTurn = haveEnemy && (((roundNum + d) & 1) == 0);
        if (enemyTurn) v = encodeMsg(MSG_ENEMY, enemyBig);
        else if (haveKing) v = encodeMsg(MSG_KING, bestKing);
        else if (d == probe) v = 0;
        else continue;
        snprintf(buf, sizeof buf, "SONAR %c %llu\n", DCH[d], (unsigned long long)v);
        emit(buf);
    }
}

// ---------------------------------------------------------------- roles
#ifndef P_R_GATHER
#define P_R_GATHER 340
#endif
#ifndef P_R_FEED
#define P_R_FEED 400
#endif
#ifndef P_R_EARLYFEED
#define P_R_EARLYFEED 1000
#endif
static const int R_GATHER = P_R_GATHER;
static const int R_FEED = P_R_FEED;
static const int R_EARLYFEED = P_R_EARLYFEED;
#ifndef P_FEED_RADIUS
#define P_FEED_RADIUS 8
#endif
#ifndef P_FEED_RADIUS_LATE
#define P_FEED_RADIUS_LATE 16
#endif
static const int FEED_RADIUS = P_FEED_RADIUS;
static const int FEED_RADIUS_LATE = P_FEED_RADIUS_LATE;
#ifndef P_PIPE_START
#define P_PIPE_START 1000
#endif
#ifndef P_PIPE_R0
#define P_PIPE_R0 5
#endif
#ifndef P_PIPE_R1
#define P_PIPE_R1 30
#endif
#ifndef P_PIPE_MINLEN
#define P_PIPE_MINLEN 3
#endif
static const int PIPE_START = P_PIPE_START;
static const double PIPE_R0 = P_PIPE_R0;
static const double PIPE_R1 = P_PIPE_R1;
static const int PIPE_MINLEN = P_PIPE_MINLEN;
#ifndef P_FINALE
#define P_FINALE 470
#endif
static const int FINALE = P_FINALE;

enum Role { HARVEST, KING, FEEDER, HUNTER };
static Role role = HARVEST;
static int kingTile = -1, kingId = -1, kingDir = 0;
static bool kingVisible = false;
static int huntTile = -1;
static int rallyTile = -1, spawnTile = -1;

static bool endgame() { return roundNum >= R_GATHER; }

static double dragonValue(int len, bool king) {
    if (king) return len * KING_VAL_MUL + KING_VAL_ADD;
    return len + UNIT_VALUE;
}

// The anchor: where our king lives in the endgame, computable by every dragon
// of the team without talking. Our spawn centroid, moved to open ground.
static void computeRally() {
    rallyTile = spawnTile;
    if (!mapKnown) return;
    const mapsdata::MapDef& m = mapsdata::MAPS[mapIndex];
    const int* p = m.dragons;
    int sx = 0, sy = 0, n = 0;
    for (int i = 0; i < m.ndragons; i++) {
        int team = p[0], len = p[1];
        if (team == myTeam) { sx += p[2]; sy += p[3]; n++; }
        p += 2 + 2 * len;
    }
    if (n == 0) return;
    int c = tileOf(sx / n, sy / n);
    // nearest tile (by BFS over open edges) with at least 3 open sides
    static int dist[MAXT];
    for (int t = 0; t < T; t++) dist[t] = INF;
    int qh = 0, qt = 0;
    dist[c] = 0; bfsQ[qt++] = c;
    while (qh < qt) {
        int u = bfsQ[qh++];
        int open = 0;
        for (int d = 0; d < 4; d++) if (stepTo[u][d] >= 0) open++;
        if (open >= 3 && gmax[u] != 0) { rallyTile = u; return; }
        for (int d = 0; d < 4; d++) {
            int v = stepTo[u][d];
            if (v < 0 || dist[v] != INF) continue;
            dist[v] = dist[u] + 1;
            bfsQ[qt++] = v;
        }
    }
    rallyTile = c;
}

// ---------------------------------------------------------------- actions
struct Action {
    int kind = 0;  // 0 move, 1 split, 2 die in place (feeding)
    int dirs[8] = {0};
    int n = 0;
    int split = 0;
};

struct Eval {
    bool valid = false;
    bool dead = false;
    bool ram = false;
    bool threatened = false;
    bool trapped = false;
    double score = -1e18;
#ifdef LOCAL_DEBUG
    char why[160] = {0};
#endif
};

static bool iAmKing = false;
static double myVal = 0;
static int distGoal[MAXT];
static int goalTile = -1;

static const DragonView* findDragon(int id) {
    for (auto& dv : dragons) if (dv.id == id) return &dv;
    return nullptr;
}

static Eval evalAction(const Action& a) {
    Eval ev;
    ev.valid = true;
    double score = 0;
    std::vector<int> newBody;
    int newLen = myLen;
    if (a.kind == 2) {
        // feeding: die where I stand, next to the king's head
        ev.dead = true;
        ev.score = -1e9;
        if (role == FEEDER && kingVisible) {
            for (int d = 0; d < 4; d++)
                if (stepTo[kingTile][d] == myHeadTile) ev.score = 50.0 + myLen;
        }
        return ev;
    }
    if (a.kind == 1 && iAmKing && NO_KING_SPLIT) { ev.valid = false; return ev; }
    if (a.kind == 1) {
        int k = a.split;
        newBody = myBody;
        newLen = myLen - k;
        if ((int)newBody.size() > newLen) newBody.resize(newLen);
        // the parent stands still; is it trapped where it stands?
        int pNeed = newLen + 3;
        bool parentTrapped = spaceCount(newBody, newLen, pNeed, false) < pNeed;
        // the child: my rear k segments, reversed, head at my old tail
        bool childOk = false;
        double childPen = 0;
        if (bodyExact && (int)myBody.size() == myLen) {
            std::vector<int> child(myBody.rbegin(), myBody.rbegin() + k);
            int cNeed = k + 3;
            int cOpt = spaceCount(child, k, cNeed, false, &newBody);
            childOk = cOpt >= cNeed;
            if (!childOk) childPen = (dragonValue(k, false) + 3.0) * (1.0 - (double)cOpt / cNeed) * 1.5;
            else {
                // the child moves last in the round, after everyone else: it
                // needs room nobody else can take first
                int cT = std::max(k + 4, 8);
                int cTerr = spaceCount(child, k, cT, true, &newBody);
                if (cTerr < k + 2) { childOk = false; childPen = dragonValue(k, false) + 1.0; }
                else if (cTerr < cT) childPen = 0.6 * (1.0 - (double)cTerr / cT) * (dragonValue(k, false) + 1.0);
            }
        } else {
            childPen = 1.0;
        }
        score -= childPen;
        bool escape = parentTrapped && childOk;
        if (escape) score += 1.0;  // saving k segments beats dying with all of them
        else if (endgame() || role == KING || role == FEEDER) score -= 6.0;
        else if (k == 2 && unitCount < UNIT_TARGET) score += SPLIT_BONUS;
        else if (k == 2) score -= 2.0;  // enough heads: grow instead
        else score -= 1.0;  // unusual sizes only as escapes
        // the parent stands still this turn: it must have a way out next turn
        int exits = 0;
        for (int d = 0; d < 4; d++) {
            int q = stepTo[myHeadTile][d];
            if (q < 0 || (vis[q] && occ[q] >= 0)) continue;
            if (distEnemy[q] <= 1) continue;
            exits++;
        }
        if (!escape && exits < 2) score -= SPLIT_BONUS + 1.0;
    } else {
        SimOut so = simulateMove(a.dirs, a.n);
        if (so.dead) {
            ev.dead = true;
            if (so.killId >= 0) {
                const DragonView* e = findDragon(so.killId);
                if (e && isEnemyTeam(e->team)) {
                    double gain = dragonValue(e->len, false) - myVal + aggrNow;
                    if (unitCount <= 1) gain -= 100.0;  // my death ends the game
                    if (e->len >= 7) gain += 0.7 * (e->len - 6);  // big ones are their kings
                    if (roundNum >= FINALE && !iAmKing && e->len >= 6) gain += 2.0 * e->len;  // only the longest counts at the end
                    ev.ram = true;
                    ev.score = gain - 0.01 * a.n;
                    return ev;
                }
            }
            score = -myVal - 5.0 - (a.n > 1 ? 1.0 : 0.0);
            if (so.killId >= 0) {
                // never take a teammate down with me
                const DragonView* f = findDragon(so.killId);
                score -= 10.0 + (f ? dragonValue(f->len, false) : 5.0);
            }
            ev.score = score;
            return ev;
        }
        newBody = so.body;
        newLen = so.newLen;
        score += EAT_W * so.eaten;
        // feeders leave the pearls around the king to the king
        if (role == FEEDER && kingTile >= 0 && so.eaten > 0 && manhattan(so.body[0], kingTile) <= 4)
            score -= (EAT_W + 0.6) * so.eaten;
        // every extra step burns a segment: price it like the pearl it undoes
        score -= SPRINT_W * (a.n - 1);
        if (iAmKing && roundNum >= FINALE) score -= 3.0 * (a.n - 1);
        if (so.throughUnknown) {
            // Through a portal into tiles I cannot see: portal exits are busy
            // (both teams use them), so the exit may hold a head or a body.
            double v = dragonValue(so.newLen, iAmKing);
            double risk = PORTAL_RISK;
            if (probeKnown && a.dirs[0] == probeDir) risk = probeClear ? PROBE_CLEAR_RISK : 0.6;
            int dest = so.body.empty() ? -1 : so.body[0];
            if (dest >= 0 && lastSeen[dest] >= 0) {
                int since = roundNum - lastSeen[dest];
                if (roundNum - lastOccRound[dest] <= 3) risk = 0.8;
                else if (since <= 2) risk = std::min(risk, 0.04);
            }
            // team convention: cross portals northwards / eastwards, so two of
            // us never meet head-on through the same pair of portals
            {
                int cur = myHeadTile;
                for (int i = 0; i < a.n; i++) {
                    int et, side; edgeOf(cur, a.dirs[i], et, side);
                    if (edgeKind(et, side) == 2 && (a.dirs[i] == 2 || a.dirs[i] == 3)) { risk += PORTAL_WRONGWAY; break; }
                    cur = stepTo[cur][a.dirs[i]];
                    if (cur < 0) break;
                }
            }
            score -= 0.3 + PORTAL_W * v + risk * (v + 2.0);
            if (dest >= 0 && deadEnd[dest]) score -= v;
        }
    }
    if (newBody.empty()) { ev.valid = false; return ev; }
    int newHead = newBody[0];

    if (iAmKing && deadEnd[newHead] && !deadEnd[myHeadTile]) score -= 4.0 + 0.5 * newLen;

    // threats: enemies that can reach my new head before my next turn
    double newVal = dragonValue(newLen, iAmKing);
    double worst = 0, sum = 0;
    for (auto& dv : dragons) {
        if (dv.id == myId || dv.head < 0 || !isEnemyTeam(dv.team)) continue;
        int reach = reachOf(dv);
        int k = reachSteps(dv.head, newHead, reach, newBody);
        if (k > reach) continue;
        // Most heads only ram what they can touch in one step; sprint rams
        // (2+ steps, paid in segments) are much rarer.
        double pk = k <= 1 ? 1.0 : (k == 2 ? THR_P2 : THR_P3);
        double loss = newVal - dragonValue(dv.len, false);
        // An even trade is neutral on paper, but many teams take it and it
        // costs me the head (and its future) I was using.
        double evenCost = roundNum < EARLY_ROUNDS ? EVEN_TRADE_EARLY * newVal : 0.1;
        double pen;
        if (loss > 0.5) pen = 0.9 * loss + std::max(evenCost, EVEN_EXPOSE);
        else if (loss > -0.5) pen = std::max(evenCost, EVEN_EXPOSE);
        else pen = evenCost;
        if (unitCount + (a.kind == 1 ? 1 : 0) <= 1) pen += LAST_UNIT_PEN;  // losing my last dragon loses the game
        pen *= pk;
        worst = std::max(worst, pen);
        sum += pen;
        if (pen >= THR_MIN) ev.threatened = true;
    }
    if (iAmKing && roundNum >= FINALE) { worst *= 2.0; sum *= 2.0; }
    score -= worst + 0.2 * sum;

    // exposure: unseen tiles a few steps away may hold an attacker
    {
        double atRisk = newVal - dragonValue(3, false);
        if (atRisk > 0.5 && (iAmKing || EXPOSE_ALL)) {
            int ud = unseenDist(newHead, newBody, 3);
            static const double P[4] = {0, 0.45, 0.2, 0.06};
            if (ud <= 3) score -= atRisk * P[ud];
        }
    }

    // space: never walk into a pocket smaller than me, and prefer keeping a
    // territory nobody else can take from me
    int need = newLen + 3;
    int opt = spaceCount(newBody, newLen, need, false);
    int terr = -1;
    if (opt < need) {
        ev.trapped = true;
        // A dead end full of pearls is a mill: eat to the end, split, and let
        // the child walk out while the parent stays behind. Worth it when the
        // pocket makes me long enough to split and nobody else is in it.
        double pocket = 0;
        bool contestedPocket = false;
        for (int i = 1; i < opt; i++) {
            int t = bfsQ[i];
            if (vis[t] && pearlNow[t]) pocket += 1.0;
            else if (gmax[t] > 0 && gmax[t] <= 2) pocket += 0.7;
            if (distEnemy[t] <= 2 || distAlly[t] <= 1) contestedPocket = true;
        }
        int pocketGain = (int)pocket;
        if (a.kind == 0 && !contestedPocket && unitCount < unitLimit && newLen + pocketGain >= 4 && role != KING) {
            score -= (2.0 + UNIT_VALUE) - 0.9 * pocket;
        } else {
            score -= (newVal + 3.0) * (1.0 - (double)opt / need) * 1.5;
        }
    } else {
        double tw = iAmKing ? KING_TERR_W : TERR_W;
        if (tw > 0) {
            int needT = iAmKing ? std::max(2 * newLen, 14) : std::max(newLen + 4, 8);
            terr = spaceCount(newBody, newLen, needT, true);
            if (terr < needT) score -= tw * (1.0 - (double)terr / needT) * (newVal + 1.0);
        }
    }

    // survival: is there any way to live through the next few turns?
    if ((SURV_ON || (SURV_KING && iAmKing)) && !ev.trapped) {
        int want = iAmKing ? std::min(SURV_KING_DEPTH, newLen + 4) : std::min(SURV_DEPTH, newLen + 4);
        int got = survivalDepth(newBody, newLen, want);
        if (got < want) {
            score -= (newVal + 2.0) * (1.0 - (double)got / want) * 1.2;
            ev.trapped = true;
        }
    }

    // navigation
    if ((role == FEEDER || role == HUNTER) && distGoal[newHead] < INF) {
        score -= 0.6 * distGoal[newHead];
        score += 0.2 * POT_W * potentialAt(newHead);
    } else if ((role == FEEDER || role == HUNTER) && goalTile >= 0) {
        score -= 0.6 * (manhattan(newHead, goalTile) + 8);
    } else {
        score += POT_W * potentialAt(newHead);
        if (YIELD_W > 0) score += YIELD_W * yieldAt(newHead);
        if (iAmKing && rallyTile >= 0 && roundNum >= R_GATHER) {
            int dr = manhattan(newHead, rallyTile);
            if (dr > 6) score -= 0.15 * (dr - 6);
        }
        if (iAmKing) score += (KING_POT_MUL - 1.0) * POT_W * potentialAt(newHead);
        if (role == HARVEST && endgame() && kingTile >= 0 && kingId != myId)
            score -= 0.06 * manhattan(newHead, kingTile);
    }

    // crowding: stay out of teammates' way
    if (role == HARVEST) {
        double crowd = 0;
        for (auto& dv : dragons) {
            if (dv.id == myId || dv.head < 0 || dv.team != myTeam) continue;
            int md = manhattan(newHead, dv.head);
            if (md < 5) crowd += 5 - md;
        }
        score -= CROWD_W * crowd;
    }

    score += (double)(rnd64() % 1000) * (P_NOISE / 1000.0);
    ev.score = score;
#ifdef LOCAL_DEBUG
    snprintf(ev.why, sizeof ev.why, "thr=%.2f opt=%d need=%d terr=%d pot=%.2f", worst + 0.2 * sum, opt, need, terr,
             (role == FEEDER) ? 0.0 : potentialAt(newHead));
#endif
    return ev;
}

// ---------------------------------------------------------------- decision
static char indicator[256];
static int lastKind = -1, lastN = 0, lastDirs[8], lastHead = -1, lastLen = 0;

static void decideRole() {
    role = HARVEST;
    kingId = -1;
    kingTile = -1;
    kingVisible = false;
    huntTile = -1;
    bool kingKnown = bestKing.valid && roundNum - bestKing.round <= KING_STALE;
    if (kingKnown) {
        kingId = bestKing.id;
        kingTile = bestKing.tile;
        for (auto& dv : dragons)
            if (dv.id == kingId && dv.head >= 0) { kingTile = dv.head; kingDir = dv.dir; kingVisible = true; }
        // stale hearsay: the king will be at the anchor
        if (!kingVisible && roundNum - bestKing.round > 6 && rallyTile >= 0) kingTile = rallyTile;
    }
    bool iAmTheKing = kingKnown && kingId == myId;
    wasKing = false;
    bool saturatedTeam = unitCount >= unitLimit - 4;
    bool kingTime = roundNum >= R_GATHER || (saturatedTeam && roundNum >= R_EARLYFEED) || roundNum >= PIPE_START ||
                    roundNum >= KING_START;
    if (iAmTheKing && kingTime) { role = KING; wasKing = true; bestKing.crowned = true; return; }
    // hunters: a big enemy close by is worth a small dragon of ours
    bool finale = roundNum >= FINALE;
    if (HUNT_ON && enemyBig.valid && roundNum - enemyBig.round <= ENEMY_STALE && (myLen <= 6 || finale) &&
        manhattan(myHeadTile, enemyBig.tile) <= (finale ? 24 : 12) &&
        enemyBig.len >= std::max(finale ? 6 : 8, myLen + (finale ? 1 : 4))) {
        role = HUNTER;
        huntTile = enemyBig.tile;
        return;
    }
    // Feeding pipeline: from PIPE_START, dragons within a radius of the king
    // (growing with time) feed it; the rest keep harvesting and drift in.
    if (kingKnown && kingId != myId && roundNum >= PIPE_START) {
        int dk = manhattan(myHeadTile, kingTile);
        double f = std::min(1.0, (roundNum - PIPE_START) / std::max(1.0, 480.0 - PIPE_START));
        int radius = (int)(PIPE_R0 + (PIPE_R1 - PIPE_R0) * f);
        if (myLen >= PIPE_MINLEN && dk <= radius) { role = FEEDER; return; }
        return;
    }
    if (kingKnown && kingId != myId && roundNum >= std::min(R_EARLYFEED, R_GATHER)) {
        int dk = manhattan(myHeadTile, kingTile);
        int radius = roundNum >= 450 ? FEED_RADIUS_LATE : FEED_RADIUS;
        bool feed = roundNum >= R_FEED && dk <= radius;
        bool saturated = unitCount >= unitLimit - 4;
        bool early = roundNum >= R_EARLYFEED;
        if (!feed && early && saturated && myLen >= 5 && dk <= 10) feed = true;
        if (!feed && early && saturated && myLen >= 8) feed = true;
        if (!feed && roundNum >= R_GATHER && myLen >= 6 && dk <= radius) feed = true;
        if (feed) { role = FEEDER; return; }
    }
}

static void chooseAndEmit() {
    computeDeadEnds();
    buildFreeAt();
    {
        int srcA[256], nA = 0, srcE[256], nE = 0;
        for (auto& dv : dragons) {
            if (dv.id == myId || dv.head < 0) continue;
            if (isEnemyTeam(dv.team)) { if (nE < 256) srcE[nE++] = dv.head; }
            else if (nA < 256) srcA[nA++] = dv.head;
        }
        int me = myHeadTile;
        bfsField(&me, 1, distMe, 80);
        bfsField(srcA, nA, distAlly, 80);
        bfsField(srcE, nE, distEnemy, 80);
    }
    kingVisibleNow = false; kingTileNow = -1; kingIdNow = -1;
    if (bestKing.valid && roundNum - bestKing.round <= KING_STALE && roundNum >= KING_START) {
        kingIdNow = bestKing.id;
        for (auto& dv : dragons)
            if (dv.id == kingIdNow && dv.head >= 0) { kingVisibleNow = true; kingTileNow = dv.head; }
    }
    computeTileValues();
    {
        // Trades are attrition: good when we outnumber them here, bad when
        // they outnumber us (their spare heads refill the gap faster).
        int ah = 1, eh = 0;
        for (auto& dv : dragons) {
            if (dv.id == myId || dv.head < 0) continue;
            if (isEnemyTeam(dv.team)) eh++; else ah++;
        }
        double diff = std::max(-3, std::min(3, ah - eh)) / 3.0;
        aggrNow = AGGRESSION + AGGR_BAL * diff;
    }
    potGen++;
    yGen++;
    if (YIELD_W > 0) computeRates();
    decideRole();
    iAmKing = role == KING;
    myVal = dragonValue(myLen, iAmKing);
    if (potAvoidDeadEnds != iAmKing) { potAvoidDeadEnds = iAmKing; potGen++; }
    goalTile = -1;
    for (int t = 0; t < T; t++) distGoal[t] = INF;
    if (role == FEEDER && kingTile >= 0) {
        goalTile = kingTile;
        int srcs[4], n = 0;
        if (kingVisible) {
            for (int d = 0; d < 4; d++) {
                int q = stepTo[kingTile][d];
                if (q >= 0 && !(vis[q] && occ[q] >= 0)) srcs[n++] = q;
            }
        }
        if (n == 0) srcs[n++] = kingTile;
        // BFS from the goal, ignoring time (the goal moves anyway)
        int qh = 0, qt = 0;
        for (int i = 0; i < n; i++) { distGoal[srcs[i]] = 0; bfsQ[qt++] = srcs[i]; }
        while (qh < qt) {
            int u = bfsQ[qh++];
            for (int d = 0; d < 4; d++) {
                int v = stepTo[u][d];
                if (v < 0 || distGoal[v] != INF) continue;
                if (vis[v] && occ[v] >= 0 && parts[occ[v]].id != myId) continue;
                distGoal[v] = distGoal[u] + 1;
                bfsQ[qt++] = v;
            }
        }
    } else if (role == HUNTER && huntTile >= 0) {
        goalTile = huntTile;
        int qh = 0, qt = 0;
        distGoal[huntTile] = 0; bfsQ[qt++] = huntTile;
        while (qh < qt) {
            int u = bfsQ[qh++];
            for (int d = 0; d < 4; d++) {
                int v = stepTo[u][d];
                if (v < 0 || distGoal[v] != INF) continue;
                distGoal[v] = distGoal[u] + 1;
                bfsQ[qt++] = v;
            }
        }
    }

#ifdef LOCAL_DEBUG
    {
        char b[400];
        int k = snprintf(b, sizeof b, "LOG state head=(%d,%d) dir=%c len=%d exact=%d body=", tx(myHeadTile), ty(myHeadTile),
                         DCH[myDirection], myLen, (int)bodyExact);
        for (size_t i = 0; i < myBody.size() && i < 12 && k < 300; i++)
            k += snprintf(b + k, sizeof b - k, "(%d,%d)", tx(myBody[i]), ty(myBody[i]));
        snprintf(b + k, sizeof b - k, " role=%d king=%d@(%d,%d) len%d age%d vis%d enemy=%d@(%d,%d)len%d age%d units=%d\n",
                 (int)role, bestKing.id, bestKing.tile >= 0 ? tx(bestKing.tile) : -1, bestKing.tile >= 0 ? ty(bestKing.tile) : -1,
                 bestKing.len, roundNum - bestKing.round, (int)kingVisible, enemyBig.id,
                 enemyBig.tile >= 0 ? tx(enemyBig.tile) : -1, enemyBig.tile >= 0 ? ty(enemyBig.tile) : -1, enemyBig.len,
                 roundNum - enemyBig.round, unitCount);
        emit(b);
    }
#endif
#ifdef LOCAL_DEBUG
    if (probeKnown) {
        char b[160];
        snprintf(b, sizeof b, "LOG probe dir=%c clear=%d echoes=%d,%d,%d,%d,%d\n", DCH[probeDir], (int)probeClear,
                 echoes[0], echoes[1], echoes[2], echoes[3], echoes[4]);
        emit(b);
    }
#endif
    Action best;
    double bestScore = -1e18;
    bool anyGood = false;
    // Rams are trades: worth taking when the gain is positive relative to the
    // best thing I could do otherwise, so they are scored on top of it.
    struct RamCand { Action a; double gain; };
    std::vector<RamCand> rams;
    auto consider = [&](const Action& a, const Eval& e) {
#ifdef LOCAL_DEBUG
        {
            char b[300];
            char mv[16]; int k = 0;
            if (a.kind == 1) { mv[k++] = 'S'; mv[k++] = 'P'; }
            for (int i = 0; i < a.n; i++) mv[k++] = DCH[a.dirs[i]];
            mv[k] = 0;
            snprintf(b, sizeof b, "LOG %s v=%d sc=%.3f d=%d t=%d tr=%d %s\n", mv, (int)e.valid, e.score, (int)e.dead, (int)e.threatened, (int)e.trapped, e.why);
            emit(b);
        }
#endif
        if (!e.valid) return;
        if (RAM_REL && e.ram) { rams.push_back({a, e.score}); return; }
        if (e.score > bestScore) { bestScore = e.score; best = a; }
    };

    for (int d = 0; d < 4; d++) {
        Action a; a.kind = 0; a.n = 1; a.dirs[0] = d;
        Eval e = evalAction(a);
        consider(a, e);
        if (e.valid && !e.dead && !e.threatened && !e.trapped) anyGood = true;
    }
    if (myLen - 2 >= 2 && unitCount < unitLimit) {
        Action a; a.kind = 1; a.split = 2;
        consider(a, evalAction(a));
        if (!anyGood) {
            for (int k = 3; k <= myLen - 2; k++) {
                Action b; b.kind = 1; b.split = k;
                consider(b, evalAction(b));
            }
        }
    }
    if (role == FEEDER && kingVisible) {
        Action a; a.kind = 2;
        consider(a, evalAction(a));
    }
    // sprints: rams (cheap to find) and escapes (only when nothing is good)
    int maxSteps = std::min(myLen - 1, 4);
    for (int n = 2; n <= maxSteps; n++) {
        if (spentNs() > SOFT_BUDGET) break;
        int total = 1 << (2 * n);
        for (int code = 0; code < total; code++) {
            Action a; a.kind = 0; a.n = n;
            int c = code;
            bool reversal = false;
            for (int i = 0; i < n; i++) {
                a.dirs[i] = c & 3; c >>= 2;
                if (i > 0 && a.dirs[i] == ((a.dirs[i - 1] + 2) & 3)) reversal = true;
            }
            if (reversal) continue;
            SimOut so = simulateMove(a.dirs, a.n);
            if (so.dead) {
                if (so.killId < 0) continue;
                consider(a, evalAction(a));
                continue;
            }
            if (anyGood || spentNs() > SOFT_BUDGET) continue;
            consider(a, evalAction(a));
        }
    }

    if (RAM_REL && !rams.empty()) {
        double base = bestScore > -5.0 ? bestScore : -myVal - 5.0;
        for (auto& r : rams) {
            double s = (bestScore > -5.0 ? base + r.gain : base + r.gain + myVal);
            if (s > bestScore) { bestScore = s; best = r.a; }
        }
    }
    nextHeadTile = -1;
    if (best.kind == 1) { nextHeadTile = myHeadTile; nextFacing = myDirection; }
    else if (best.kind == 0 && best.n > 0) {
        int cur = myHeadTile;
        for (int i = 0; i < best.n && cur >= 0; i++) cur = stepTo[cur][best.dirs[i]];
        nextHeadTile = cur;
        nextFacing = best.dirs[best.n - 1];
    }
    lastKind = best.kind; lastN = best.n; lastHead = myHeadTile; lastLen = myLen;
    for (int i = 0; i < best.n; i++) lastDirs[i] = best.dirs[i];
    prevActKind = best.kind; prevActN = best.n;
    for (int i = 0; i < best.n; i++) prevActDirs[i] = best.dirs[i];
    if (best.kind == 0 && best.n == 0) { prevActN = 1; prevActDirs[0] = 0; }
    char buf[64];
    if (best.kind == 2) {
        emit("SPLIT 1\n");  // illegal on purpose: the dragon dies where it stands
    } else if (best.kind == 1) {
        snprintf(buf, sizeof buf, "SPLIT %d\n", best.split);
        emit(buf);
    } else {
        int k = 0;
        memcpy(buf, "MOVE ", 5); k = 5;
        if (best.n == 0) buf[k++] = 'N';
        for (int i = 0; i < best.n; i++) buf[k++] = DCH[best.dirs[i]];
        buf[k++] = '\n'; buf[k] = 0;
        emit(buf);
    }
#ifdef LOCAL_DEBUG
    snprintf(indicator, sizeof indicator, "INDICATOR r%d role%d len%d sc%.2f\n", roundNum, (int)role, myLen, bestScore);
    emit(indicator);
#endif
}

#ifdef VERIFY
static void verifyPrediction() {
    if (lastKind != 0 || lastHead < 0 || myHeadTile < 0) return;
    int cur = lastHead;
    for (int i = 0; i < lastN; i++) {
        int nx = stepTo[cur][lastDirs[i]];
        if (nx < 0) return;
        cur = nx;
    }
    if (cur != myHeadTile) {
        char b[256];
        snprintf(b, sizeof b, "HEAD MISMATCH from (%d,%d) predicted (%d,%d) actual (%d,%d) len %d->%d",
                 tx(lastHead), ty(lastHead), tx(cur), ty(cur), tx(myHeadTile), ty(myHeadTile), lastLen, myLen);
        vlog(b);
    }
}
#endif

// ---------------------------------------------------------------- main
int main() {
    setvbuf(stdout, nullptr, _IOFBF, 1 << 16);
    for (int i = 0; i < 256; i++) { powLam[i] = std::pow(LAMBDA, i); powFar[i] = std::pow(FAR_LAMBDA, i); powKeep[i] = std::pow(0.985, i); powY[i] = std::pow(P_YIELD_LAMBDA, i); powBest[i] = std::pow(P_BEST_LAMBDA, i); }
    if (!readLine()) return 0;
    tokenize(); myId = (int)toInt(toks[1]);
    readLine(); tokenize(); myTeam = toks[1][0] == 'A' ? 0 : 1;
    readLine(); tokenize(); W = (int)toInt(toks[1]); H = (int)toInt(toks[2]); T = W * H;
    readLine(); tokenize(); unitLimit = (int)toInt(toks[1]);
    #ifndef P_SEED
#define P_SEED 0
#endif
    rngState ^= (uint64_t)(myId + 1) * 0x9E3779B97F4A7C15ULL ^ (uint64_t)(P_SEED) * 0xD1B54A32D192ED03ULL;
    for (int t = 0; t < T; t++) { lastSeen[t] = -1; spawnAt[t] = -1; pearlMem[t] = false; neverSpawns[t] = false; vis[t] = false; lastOccRound[t] = -1000; }
    for (int i = 0; i < 49; i++) visList[i] = 0;
    for (int i = 0; i < mapsdata::NMAPS; i++)
        if (mapsdata::MAPS[i].w == W && mapsdata::MAPS[i].h == H) candidates.push_back(i);
    if (candidates.empty()) { initLearned(); mapIndex = -2; identified = true; }
    else loadEmbedded(candidates[0]);

    while (readTurn()) {
        turnStartNs = nowNs();
        outLen = 0;
        identifyMap();
        learnFromObservations();
        buildDragons();
        updateMyBody();
#ifdef VERIFY
        verifyPrediction();
#endif
        if (turnsAlive == 0) spawnTile = myHeadTile;
        if (rallyTile < 0 || turnsAlive == 0) computeRally();
        updateMemory();
        updateDensity();
        processComms();
        readProbe();
        chooseAndEmit();
        emitSonar();
        emit("PROTOCOL 3\nENDTURN\n");
        fwrite(outBuf, 1, outLen, stdout);
        fflush(stdout);
        turnsAlive++;
    }
    return 0;
}
