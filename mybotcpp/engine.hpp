// Engine layer: geometry, static map (embedded or learned), protocol I/O,
// perception of the 7x7 window, and exact simulation of our own moves.
// Everything here was verified against the real engine (tools/, VERIFY build).
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>

#include "maps_data.hpp"

// ---------------------------------------------------------------- geometry
constexpr int MAXT = 64 * 64;
constexpr int DX[4] = {0, 1, 0, -1};
constexpr int DY[4] = {-1, 0, 1, 0};
constexpr char DCH[4] = {'N', 'E', 'S', 'W'};
constexpr int INF = 1 << 28;

static int dirOf(char c) {
    switch (c) {
    case 'N': return 0;
    case 'E': return 1;
    case 'S': return 2;
    default: return 3;
    }
}

static int W, H, T;
static inline int tileOf(int x, int y) {
    x %= W; if (x < 0) x += W;
    y %= H; if (y < 0) y += H;
    return y * W + x;
}
static inline int tx(int t) { return t % W; }
static inline int ty(int t) { return t / W; }
static inline int wrapDist(int a, int b, int n) {
    int d = a - b; if (d < 0) d = -d;
    return d < n - d ? d : n - d;
}
static inline int manhattan(int a, int b) { return wrapDist(tx(a), tx(b), W) + wrapDist(ty(a), ty(b), H); }
static inline int chebyshev(int a, int b) {
    int dx = wrapDist(tx(a), tx(b), W), dy = wrapDist(ty(a), ty(b), H);
    return dx > dy ? dx : dy;
}
// Plain grid neighbour, ignoring edges.
static inline int gridNb(int t, int d) { return tileOf(tx(t) + DX[d], ty(t) + DY[d]); }

// ---------------------------------------------------------------- rng
static uint64_t rngState = 88172645463325252ULL;
static inline uint64_t rnd64() {
    rngState ^= rngState << 7;
    rngState ^= rngState >> 9;
    return rngState;
}

// ---------------------------------------------------------------- static map
// Edge kinds: 0 open, 1 kelp, 2 portal, -1 unknown (learned maps only).
static int8_t edgeN[MAXT], edgeW[MAXT];
static int portalN[MAXT], portalW[MAXT];   // portal id or -1
static int16_t gmin[MAXT], gmax[MAXT];     // spawn gaps, -1 unknown
static int stepTo[MAXT][4];                // destination tile or -1 (blocked/unknown)
static bool mapKnown = false;              // an embedded map was identified
static int mapIndex = -1;
static char symType = 0;                   // 'x','y','r' (xy rotation) or 0 unknown

static int mirrorTile(int t) {
    int x = tx(t), y = ty(t);
    switch (symType) {
    case 'y': return tileOf(W - 1 - x, y);
    case 'x': return tileOf(x, H - 1 - y);
    case 'r': return tileOf(W - 1 - x, H - 1 - y);
    default: return -1;
    }
}

// Edge crossed when leaving tile t in direction d, as (tile, side) with side 0 = north, 1 = west.
static inline void edgeOf(int t, int d, int& et, int& side) {
    switch (d) {
    case 0: et = t; side = 0; break;
    case 2: et = gridNb(t, 2); side = 0; break;
    case 3: et = t; side = 1; break;
    default: et = gridNb(t, 1); side = 1; break;
    }
}
static inline int edgeKind(int et, int side) { return side == 0 ? edgeN[et] : edgeW[et]; }
static inline int edgePortal(int et, int side) { return side == 0 ? portalN[et] : portalW[et]; }

// Portal endpoints by id.
struct PortalEnd { int t, side; };
static std::vector<std::vector<PortalEnd>> portalEnds;

static void addPortalEnd(int id, int t, int side) {
    if (id < 0) return;
    if ((int)portalEnds.size() <= id) portalEnds.resize(id + 1);
    for (auto& e : portalEnds[id]) if (e.t == t && e.side == side) return;
    portalEnds[id].push_back({t, side});
}

static void computeSteps() {
    for (int t = 0; t < T; t++) {
        for (int d = 0; d < 4; d++) {
            int et, side;
            edgeOf(t, d, et, side);
            int k = edgeKind(et, side);
            if (k == 1) { stepTo[t][d] = -1; continue; }
            if (k == 2) {
                int id = edgePortal(et, side);
                int dest = -1;
                if (id >= 0 && id < (int)portalEnds.size() && portalEnds[id].size() == 2) {
                    PortalEnd o = portalEnds[id][0];
                    if (o.t == et && o.side == side) o = portalEnds[id][1];
                    if (o.side == 0) dest = (d == 0) ? gridNb(o.t, 0) : o.t;   // horizontal edge
                    else dest = (d == 3) ? gridNb(o.t, 3) : o.t;               // vertical edge
                }
                stepTo[t][d] = dest;
                continue;
            }
            stepTo[t][d] = gridNb(t, d);  // open or unknown: assume open
        }
    }
}

static void loadEmbedded(int idx) {
    const mapsdata::MapDef& m = mapsdata::MAPS[idx];
    mapIndex = idx;
    mapKnown = true;
    const char* alph = mapsdata::ALPH;
    int pos[128];
    for (int i = 0; i < 128; i++) pos[i] = -1;
    for (int i = 0; alph[i]; i++) pos[(unsigned char)alph[i]] = i;
    for (int t = 0; t < T; t++) {
        int p = pos[(unsigned char)m.tiles[t]];
        gmin[t] = m.pal[2 * p];
        gmax[t] = m.pal[2 * p + 1];
        char n = m.edges[2 * t], w = m.edges[2 * t + 1];
        edgeN[t] = n == '.' ? 0 : (n == 'w' ? 1 : 2);
        edgeW[t] = w == '.' ? 0 : (w == 'w' ? 1 : 2);
        portalN[t] = portalW[t] = -1;
    }
    portalEnds.clear();
    for (int i = 0; i < m.nportals; i++) {
        int t = m.portals[3 * i], side = m.portals[3 * i + 1], id = m.portals[3 * i + 2];
        if (side == 0) portalN[t] = id; else portalW[t] = id;
        addPortalEnd(id, t, side);
    }
    if (!strcmp(m.sym, "xy")) symType = 'r';
    else if (!strcmp(m.sym, "y")) symType = 'y';
    else if (!strcmp(m.sym, "x")) symType = 'x';
    else symType = 0;
    computeSteps();
}

static void initLearned() {
    mapKnown = false;
    mapIndex = -1;
    for (int t = 0; t < T; t++) {
        edgeN[t] = edgeW[t] = -1;
        portalN[t] = portalW[t] = -1;
        gmin[t] = gmax[t] = -1;
    }
    portalEnds.clear();
    computeSteps();
}

// ---------------------------------------------------------------- input
static char lineBuf[1 << 16];
static bool readLine() {
    for (;;) {
        if (!fgets(lineBuf, sizeof lineBuf, stdin)) return false;
        char* hash = strchr(lineBuf, '#');
        if (hash) *hash = 0;
        char* p = lineBuf;
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (*p) return true;
    }
}
// Splits lineBuf into tokens in place.
static char* toks[64];
static int ntok;
static void tokenize() {
    ntok = 0;
    char* p = lineBuf;
    for (;;) {
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (!*p || ntok >= 64) break;
        toks[ntok++] = p;
        while (*p && *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') p++;
        if (*p) *p++ = 0;
    }
}
static long long toInt(const char* s) { return strtoll(s, nullptr, 10); }

// ---------------------------------------------------------------- game state
static int myId = 0, myTeam = 0, unitLimit = 64;
static int roundNum = 0, myDirection = 0, myLen = 3, unitCount = 1;
static std::vector<uint64_t> sonarIn;
static int echoes[5];
static int turnsAlive = 0;  // turns this process has played

// Visible tiles this turn.
static bool vis[MAXT];
static int visList[49];
static int winX0, winY0;
static bool pearlNow[MAXT];
static int countdownNow[MAXT];

// Memory.
static int lastSeen[MAXT];       // round last seen, -1 never
static bool pearlMem[MAXT];      // pearl present when last seen
static int spawnAt[MAXT];        // absolute round of next spawn attempt, -1 unknown
static bool neverSpawns[MAXT];   // learned: countdown -1 observed

struct Part { int id, team, dir; bool head; };
static int occ[MAXT];            // index into parts, -1 empty (only meaningful when vis)
static std::vector<Part> parts;
static std::vector<int> partTile;

struct DragonView {
    int id, team;
    int head;              // head tile or -1 if not visible
    int dir;               // head facing
    std::vector<int> chain;  // tiles from head backward, as far as visible and linked
    int nparts;            // visible parts of this dragon
    bool exact;            // whole body seen
    int len;               // estimated length (lower bound unless exact)
};
static std::vector<DragonView> dragons;
static int dragonIdx[4096];  // per tile: index of dragon occupying it, -1

static std::vector<int> myBody;   // head first
static std::vector<int> prevBody;
static bool bodyExact = false;

// Output buffer.
static char outBuf[1 << 16];
static int outLen = 0;
static void emit(const char* s) {
    int n = (int)strlen(s);
    if (outLen + n < (int)sizeof outBuf) { memcpy(outBuf + outLen, s, n); outLen += n; }
}

#ifdef VERIFY
static void vlog(const char* msg) {
    FILE* f = fopen("verify.log", "a");
    if (!f) return;
    fprintf(f, "map=%d(%s) id=%d r=%d: %s\n", mapIndex, mapIndex >= 0 ? mapsdata::MAPS[mapIndex].name : "?", myId, roundNum, msg);
    fclose(f);
}
#endif

// ---------------------------------------------------------------- map identification
static std::vector<int> candidates;
static bool identified = false;

// Observed edges this turn, for verification: (tile, side, kind, portal)
struct EdgeObs { int t, side, kind, portal; };
static std::vector<EdgeObs> edgeObs;
static std::vector<std::pair<int, int>> tileObs;  // tile, countdown

static bool matchesMap(int idx) {
    const mapsdata::MapDef& m = mapsdata::MAPS[idx];
    if (m.w != W || m.h != H) return false;
    const char* alph = mapsdata::ALPH;
    for (auto& e : edgeObs) {
        char c = m.edges[2 * e.t + e.side];
        int k = c == '.' ? 0 : (c == 'w' ? 1 : 2);
        if (k != e.kind) return false;
    }
    for (auto& o : tileObs) {
        int p = (int)(strchr(alph, m.tiles[o.first]) - alph);
        int mx = m.pal[2 * p + 1];
        if ((o.second < 0) != (mx == 0)) return false;
    }
    return true;
}

static void identifyMap() {
    if (identified) {
        if (mapKnown && !matchesMap(mapIndex)) {
            // The world disagrees with the embedded map: fall back to learning.
#ifdef VERIFY
            vlog("embedded map mismatch -> learned mode");
#endif
            initLearned();
        }
        return;
    }
    std::vector<int> keep;
    for (int c : candidates) if (matchesMap(c)) keep.push_back(c);
    candidates = keep;
    if (candidates.size() == 1) {
        loadEmbedded(candidates[0]);
        identified = true;
    } else if (candidates.empty()) {
        if (mapKnown || mapIndex != -2) initLearned();
        mapIndex = -2;
        identified = true;
    } else {
        // Ambiguous for now: plan with the first candidate.
        loadEmbedded(candidates[0]);
    }
}

static void learnFromObservations() {
    if (mapKnown) return;
    bool changed = false;
    for (auto& e : edgeObs) {
        int8_t& k = e.side == 0 ? edgeN[e.t] : edgeW[e.t];
        int& pid = e.side == 0 ? portalN[e.t] : portalW[e.t];
        if (k != e.kind || pid != e.portal) {
            k = (int8_t)e.kind;
            pid = e.portal;
            changed = true;
            if (e.kind == 2) addPortalEnd(e.portal, e.t, e.side);
        }
    }
    for (auto& o : tileObs) {
        if (o.second < 0) { neverSpawns[o.first] = true; gmin[o.first] = gmax[o.first] = 0; }
    }
    if (changed) computeSteps();
}

// ---------------------------------------------------------------- turn parsing
static bool readTurn() {
    if (!readLine()) return false;
    tokenize();
    if (ntok < 1) return false;
    if (!strcmp(toks[0], "ENDGAME")) return false;
    if (strcmp(toks[0], "ROUND") || ntok < 2) return false;
    roundNum = (int)toInt(toks[1]);
    readLine(); tokenize(); myDirection = dirOf(toks[1][0]);
    readLine(); tokenize(); myLen = (int)toInt(toks[1]);
    readLine(); tokenize(); unitCount = (int)toInt(toks[1]);
    readLine(); tokenize(); int nm = (int)toInt(toks[1]);
    sonarIn.clear();
    for (int i = 0; i < nm; i++) {
        readLine(); tokenize();
        sonarIn.push_back(strtoull(toks[0], nullptr, 10));
    }
    readLine(); tokenize();
    memset(echoes, 0, sizeof echoes);
    if (!strcmp(toks[0], "ECHOES")) {
        for (int i = 0; i < 5 && i + 1 < ntok; i++) echoes[i] = (int)toInt(toks[i + 1]);
        readLine(); tokenize();
    }
    // 49 tiles
    for (int t : visList) vis[t] = false;
    tileObs.clear();
    for (int i = 0; i < 49; i++) {
        if (i > 0) { readLine(); tokenize(); }
        int x = (int)toInt(toks[0]), y = (int)toInt(toks[1]);
        int hp = (int)toInt(toks[2]), cd = (int)toInt(toks[3]);
        if (i == 0) { winX0 = x; winY0 = y; }
        int t = tileOf(x, y);
        visList[i] = t;
        vis[t] = true;
        pearlNow[t] = hp != 0;
        countdownNow[t] = cd;
        tileObs.push_back({t, cd});
    }
    readLine(); tokenize();
    int nb = (int)toInt(toks[1]);
    parts.clear();
    partTile.clear();
    for (int t : visList) occ[t] = -1;
    int myHead = -1;
    for (int i = 0; i < nb; i++) {
        readLine(); tokenize();
        Part p;
        p.team = toks[0][0] == 'A' ? 0 : 1;
        p.id = (int)toInt(toks[1]);
        int x = (int)toInt(toks[2]), y = (int)toInt(toks[3]);
        p.dir = dirOf(toks[4][0]);
        p.head = toInt(toks[5]) != 0;
        int t = tileOf(x, y);
        occ[t] = (int)parts.size();
        parts.push_back(p);
        partTile.push_back(t);
        if (p.id == myId && p.head) myHead = t;
    }
    (void)myHead;
    edgeObs.clear();
    for (int j = 0; j < 8; j++) {
        readLine(); tokenize();
        for (int c = 0; c < 7 && c < ntok; c++) {
            int t = tileOf(winX0 + c, winY0 + j);
            const char* s = toks[c];
            int kind = s[0] == '.' ? 0 : (s[0] == 'w' ? 1 : 2);
            edgeObs.push_back({t, 0, kind, kind == 2 ? (int)toInt(s) : -1});
        }
    }
    for (int j = 0; j < 7; j++) {
        readLine(); tokenize();
        for (int c = 0; c < 8 && c < ntok; c++) {
            int t = tileOf(winX0 + c, winY0 + j);
            const char* s = toks[c];
            int kind = s[0] == '.' ? 0 : (s[0] == 'w' ? 1 : 2);
            edgeObs.push_back({t, 1, kind, kind == 2 ? (int)toInt(s) : -1});
        }
    }
    return true;
}

// ---------------------------------------------------------------- perception
static int myHeadTile = -1;

// The tile behind segment at tile q (towards the tail) is a tile r whose part
// faces q. Returns the neighbour list candidates.
static void buildDragons() {
    dragons.clear();
    for (int t : visList) dragonIdx[t] = -1;
    // group
    std::vector<int> ids;
    for (auto& p : parts) ids.push_back(p.id);
    std::sort(ids.begin(), ids.end());
    ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
    for (int id : ids) {
        DragonView dv;
        dv.id = id;
        dv.team = -1;
        dv.head = -1;
        dv.dir = 0;
        dv.nparts = 0;
        dv.exact = false;
        for (size_t i = 0; i < parts.size(); i++) {
            if (parts[i].id != id) continue;
            dv.team = parts[i].team;
            dv.nparts++;
            dragonIdx[partTile[i]] = (int)dragons.size();
            if (parts[i].head) { dv.head = partTile[i]; dv.dir = parts[i].dir; }
        }
        if (dv.head >= 0) {
            dv.chain.push_back(dv.head);
            int cur = dv.head;
            for (;;) {
                int nxt = -1;
                bool unknownNb = false;
                for (int d = 0; d < 4; d++) {
                    int q = stepTo[cur][d];
                    if (q < 0) {
                        // could be a portal we do not know; treat as unknown
                        int et, side; edgeOf(cur, d, et, side);
                        if (edgeKind(et, side) != 1) unknownNb = true;
                        continue;
                    }
                    if (!vis[q]) { unknownNb = true; continue; }
                    int oi = occ[q];
                    if (oi < 0) continue;
                    const Part& op = parts[oi];
                    if (op.id != id || op.head) continue;
                    if (stepTo[q][op.dir] != cur) continue;
                    if (std::find(dv.chain.begin(), dv.chain.end(), q) != dv.chain.end()) continue;
                    nxt = q;
                    break;
                }
                if (nxt < 0) {
                    dv.exact = !unknownNb;
                    break;
                }
                dv.chain.push_back(nxt);
                cur = nxt;
                if ((int)dv.chain.size() > 64 * 64) break;
            }
        }
        dv.len = std::max<int>(dv.nparts, (int)dv.chain.size());
        if (dv.head >= 0 && dv.exact && (int)dv.chain.size() < dv.nparts) dv.exact = false;
        dragons.push_back(dv);
    }
}

// My own body. Dead reckoning from the previous exact body and my last action
// is exact even when the body runs out of sight or through a portal: after a
// move the body is a prefix of [new head positions..., old body...] with the
// reported length; after a split it is the old body cut to the new length.
// Vision is the fallback (first turn of a process, or any disagreement).
static int prevActKind = -1;          // 0 move, 1 split, -1 none/unknown
static int prevActDirs[8], prevActN = 0;
static bool prevBodyExact = false;

static void updateMyBody() {
    prevBody = myBody;
    prevBodyExact = bodyExact;
    myBody.clear();
    const DragonView* me = nullptr;
    for (auto& d : dragons) if (d.id == myId) me = &d;
    if (!me || me->head < 0) { bodyExact = false; return; }
    myHeadTile = me->head;

    if (prevBodyExact && !prevBody.empty() && prevActKind >= 0) {
        std::vector<int> pred;
        if (prevActKind == 0) {
            std::vector<int> path;
            int cur = prevBody[0];
            bool ok = true;
            for (int i = 0; i < prevActN; i++) {
                cur = stepTo[cur][prevActDirs[i]];
                if (cur < 0) { ok = false; break; }
                path.push_back(cur);
            }
            if (ok) {
                for (int i = (int)path.size() - 1; i >= 0; i--) pred.push_back(path[i]);
                for (int t : prevBody) pred.push_back(t);
            }
        } else {
            pred = prevBody;
        }
        if ((int)pred.size() >= myLen && !pred.empty() && pred[0] == myHeadTile) {
            pred.resize(myLen);
            // every visible part of mine must be on the predicted body
            bool consistent = true;
            for (size_t i = 0; i < parts.size() && consistent; i++) {
                if (parts[i].id != myId) continue;
                if (std::find(pred.begin(), pred.end(), partTile[i]) == pred.end()) consistent = false;
            }
            if (consistent) {
                myBody = pred;
                bodyExact = true;
                return;
            }
        }
    }

    myBody = me->chain;
    // My facing points from my neck to my head, so the neck is known even
    // when it lies out of sight (e.g. on the far side of a portal).
    if (myBody.size() == 1 && myLen >= 2) {
        int neck = stepTo[myHeadTile][(myDirection + 2) & 3];
        if (neck >= 0 && neck != myHeadTile) myBody.push_back(neck);
    }
    if ((int)myBody.size() >= myLen) {
        myBody.resize(myLen);
        bodyExact = true;
        return;
    }
    // extend from the previous body beyond vision
    int last = myBody.back();
    auto it = std::find(prevBody.begin(), prevBody.end(), last);
    if (it != prevBody.end()) {
        for (++it; it != prevBody.end() && (int)myBody.size() < myLen; ++it) {
            if (std::find(myBody.begin(), myBody.end(), *it) != myBody.end()) break;
            myBody.push_back(*it);
        }
    }
    bodyExact = (int)myBody.size() == myLen;
}

static int lastOccRound[MAXT];   // last round a dragon part was seen on the tile
static void updateMemory() {
    for (int i = 0; i < 49; i++) {
        int t = visList[i];
        if (lastSeen[t] < 0) lastOccRound[t] = -1000;
        if (occ[t] >= 0 && parts[occ[t]].id != myId) lastOccRound[t] = roundNum;
        lastSeen[t] = roundNum;
        pearlMem[t] = pearlNow[t];
        int cd = countdownNow[t];
        if (cd >= 0) {
            spawnAt[t] = roundNum + cd;
            int m = mirrorTile(t);
            if (m >= 0 && !vis[m]) spawnAt[m] = roundNum + cd;
        } else {
            spawnAt[t] = -1;
            neverSpawns[t] = true;
        }
    }
}

// ---------------------------------------------------------------- simulation
struct SimOut {
    bool dead = false;
    int killId = -1;          // enemy (or ally) head rammed
    int eaten = 0;
    int newLen = 0;
    std::vector<int> body;    // head first, as far as known
    bool throughUnknown = false;
};

static bool pearlAtSim(int t, const std::vector<int>& eatenTiles) {
    if (!vis[t]) return false;
    if (!pearlNow[t]) return false;
    return std::find(eatenTiles.begin(), eatenTiles.end(), t) == eatenTiles.end();
}

static SimOut simulateMove(const int* dirs, int n) {
    SimOut o;
    std::vector<int> body = myBody;
    int len = myLen;
    std::vector<int> eatenTiles;
    for (int i = 0; i < n; i++) {
        if (i > 0 && len < 3) { o.dead = true; break; }
        int cur = body.empty() ? myHeadTile : body[0];
        int dest = stepTo[cur][dirs[i]];
        if (dest < 0) { o.dead = true; break; }
        if (!vis[dest]) o.throughUnknown = true;
        if (std::find(body.begin(), body.end(), dest) != body.end()) { o.dead = true; break; }
        if (vis[dest] && occ[dest] >= 0) {
            const Part& p = parts[occ[dest]];
            if (p.id != myId) {
                if (p.head) o.killId = p.id;
                o.dead = true;
                break;
            }
        }
        body.insert(body.begin(), dest);
        bool ate = pearlAtSim(dest, eatenTiles);
        if (ate) { eatenTiles.push_back(dest); o.eaten++; len++; }
        else if ((int)body.size() > 0) {
            if ((int)body.size() > len) body.pop_back();
        }
        if (i > 0) {
            len--;
            while ((int)body.size() > len) body.pop_back();
        }
        while ((int)body.size() > len) body.pop_back();
    }
    o.newLen = len;
    o.body = body;
    return o;
}


// ---------------------------------------------------------------- clock
// In the judge the clock advances one nanosecond per CPU point spent.
static inline long long nowNs() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}
