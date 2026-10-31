// Meridian 59, Copyright 1994-2012 Andrew Kirmse and Chris Kirmse.
// All rights reserved.
//
// This software is distributed under a license that is described in
// the LICENSE file that accompanies it.
//
// Meridian is a registered trademark.
/*
 * navgrid.c
 *
 * Ours (ADR 0029). A room's nav grid: cells half a square apart, on the squares' centres and
 * corners. A cell is walkable where it stands in a sector with headroom and no wall that blocks
 * is within NAV_CLEARANCE. Neighbours are linked when a monster could walk from one centre to
 * the other: the line is walked in short sub-steps through BSPCanMoveInRoomTree, the same
 * check (walls only) every real step makes, so a path never leads where the server would
 * refuse the step. Objects that block aren't in the grid; the step still checks them.
 *
 * Paths: a search goes out from the goal over the reversed links (Dijkstra), and every cell it
 * reaches learns its next step toward the goal. Monsters chasing the same target share it, so a
 * room keeps the last few (NAV_FIELDS) for a second.
 *
 * Built the first time a room is asked for a path; freed with the room, and dropped when its
 * shape changes (BSPMoveSector, BSPChangeTexture).
 */

#include "blakserv.h"
#include <vector>
#include <float.h>

#ifdef BLAK_PLATFORM_WINDOWS
#include <direct.h>
#else
#include <sys/stat.h>
#endif

#define NAV_SUBSTEP   128.0f    // a line is checked in sub-steps of this length, as a monster walks
#define NAV_RAYS      8         // short steps a cell's clearance is checked along
#define NAV_SNAP      4         // how many cells away a point may be snapped to the grid
#define NAV_LOOKAHEAD 24        // how far along a path the next point may be pulled
#define NAV_SF_DEPTH  0x03      // roofile.c SF_MASK_DEPTH: the sector is water
#define NAV_MIN_STEP  400.0f    // a waypoint is at least this far off on one axis: beyond
                                // monster.kod's PATH_REACHED (24 fine units, 384 here)

// neighbour offsets by link bit: E, SE, S, SW, W, NW, N, NE
static const int DirCol[8] = { 1, 1, 0, -1, -1, -1, 0, 1 };
static const int DirRow[8] = { 0, 1, 1, 1, 0, -1, -1, -1 };

static UINT64 navWindowStart = 0;
static int    navWindowSearches = 0;

/*****************************************************************************************/

static void* NavAlloc(size_t size)
{
   void* p = AllocateMemory(MALLOC_ID_ROOM, size);
   memset(p, 0, size);
   return p;
}

static void NavFieldsFree(NavGrid* g)
{
   int n = g->Cols * g->Rows;
   for (int k = 0; k < NAV_FIELDS; k++)
   {
      if (g->Fields[k].Next)
         FreeMemory(MALLOC_ID_ROOM, g->Fields[k].Next, n);
      g->Fields[k].Next = NULL;
      g->Fields[k].Goal = -1;
   }
}

void NavFreeGrid(room_type* Room)
{
   NavGrid* g = Room->Nav;
   if (!g)
      return;

   int n = g->Cols * g->Rows;
   NavFieldsFree(g);
   FreeMemory(MALLOC_ID_ROOM, g->Walk, n);
   FreeMemory(MALLOC_ID_ROOM, g->Links, n);
   FreeMemory(MALLOC_ID_ROOM, g->Flags, n);
   FreeMemory(MALLOC_ID_ROOM, g->Area, n * sizeof(unsigned short));
   FreeMemory(MALLOC_ID_ROOM, g, sizeof(NavGrid));
   Room->Nav = NULL;
}

static inline V2 NavCentre(const NavGrid* g, int cell)
{
   V2 p;
   p.X = (float)(cell % g->Cols) * NAV_CELL;
   p.Y = (float)(cell / g->Cols) * NAV_CELL;
   return p;
}

static inline int NavCellAt(const NavGrid* g, float x, float y)
{
   int c = (int)floorf(x / NAV_CELL + 0.5f);
   int r = (int)floorf(y / NAV_CELL + 0.5f);
   if (c < 0 || r < 0 || c >= g->Cols || r >= g->Rows)
      return -1;
   return r * g->Cols + c;
}

/*****************************************************************************************/
/* NavCanWalkLine: whether a monster could walk straight from S to E, walls only: the line in */
/*                 short sub-steps, each through the same check as a real step.               */
/*****************************************************************************************/
bool NavCanWalkLine(room_type* Room, const V2* S, const V2* E)
{
   if (!Room || Room->TreeNodesCount == 0)
      return false;

   const float dx = E->X - S->X;
   const float dy = E->Y - S->Y;
   int steps = (int)ceilf(sqrtf(dx * dx + dy * dy) / NAV_SUBSTEP);
   if (steps < 1)
      steps = 1;

   V2 a = *S;
   for (int k = 1; k <= steps; k++)
   {
      V2 b;
      b.X = S->X + dx * (float)k / (float)steps;
      b.Y = S->Y + dy * (float)k / (float)steps;

      Wall* blockWall = NULL;
      if (!BSPCanMoveInRoomTree(&Room->TreeNodes[0], &a, &b, &blockWall))
         return false;
      a = b;
   }
   return true;
}

/*****************************************************************************************/
/* Building                                                                               */
/*****************************************************************************************/

// connected areas over two-way links, numbered largest first
static void NavLabelAreas(NavGrid* g)
{
   const int n = g->Cols * g->Rows;
   int* label = (int*)malloc(n * sizeof(int));
   int* stack = (int*)malloc(n * sizeof(int));
   int* sizes = (int*)malloc((n + 1) * sizeof(int));
   int areas = 0;

   for (int i = 0; i < n; i++)
      label[i] = -1;

   for (int s = 0; s < n; s++)
   {
      if (!g->Walk[s] || label[s] >= 0)
         continue;

      int id = areas++;
      int size = 0, top = 0;
      label[s] = id;
      stack[top++] = s;
      while (top)
      {
         int i = stack[--top];
         size++;
         for (int d = 0; d < 8; d++)
         {
            if (!(g->Links[i] & (1 << d)))
               continue;
            int j = i + DirRow[d] * g->Cols + DirCol[d];
            if (label[j] < 0 && (g->Links[j] & (1 << ((d + 4) & 7))))
            {
               label[j] = id;
               stack[top++] = j;
            }
         }
      }
      sizes[id] = size;
   }

   // rank areas by size, largest first (stable, as the client's sort)
   int* order = (int*)malloc((areas + 1) * sizeof(int));
   int* rank = (int*)malloc((areas + 1) * sizeof(int));
   for (int k = 0; k < areas; k++)
      order[k] = k;
   std::stable_sort(order, order + areas, [sizes](int a, int b) { return sizes[a] > sizes[b]; });
   for (int k = 0; k < areas; k++)
      rank[order[k]] = k + 1;

   for (int i = 0; i < n; i++)
      g->Area[i] = (label[i] >= 0) ? (unsigned short)std::min(65535, rank[label[i]]) : 0;

   free(order);
   free(rank);
   free(sizes);
   free(stack);
   free(label);
}

static NavGrid* NavBuild(room_type* Room)
{
   UINT64 start = GetMilliCount();

   NavGrid* g = (NavGrid*)NavAlloc(sizeof(NavGrid));
   g->Cols = std::max(1, (int)ceilf(Room->ThingsBox.Max.X / NAV_CELL) + 1);
   g->Rows = std::max(1, (int)ceilf(Room->ThingsBox.Max.Y / NAV_CELL) + 1);
   for (int k = 0; k < NAV_FIELDS; k++)
      g->Fields[k].Goal = -1;

   const int cols = g->Cols, rows = g->Rows, n = cols * rows;
   g->Walk = (unsigned char*)NavAlloc(n);
   g->Links = (unsigned char*)NavAlloc(n);
   g->Flags = (unsigned char*)NavAlloc(n);
   g->Area = (unsigned short*)NavAlloc(n * sizeof(unsigned short));
   float* floors = (float*)malloc(n * sizeof(float));

   if (Room->TreeNodesCount == 0)
   {
      free(floors);
      return g;
   }

   // The move check already refuses a step ending within WALLMINDISTANCE of a wall that
   // blocks, so a short step out from the centre each way checks the rest of the clearance.
   const float reach = std::max(1.0f, NAV_CLEARANCE - WALLMINDISTANCE);
   float rayX[NAV_RAYS], rayY[NAV_RAYS];
   for (int k = 0; k < NAV_RAYS; k++)
   {
      rayX[k] = cosf((float)k * PI_MULT_2 / NAV_RAYS) * reach;
      rayY[k] = sinf((float)k * PI_MULT_2 / NAV_RAYS) * reach;
   }

   // cells: in a sector, with headroom, no wall that blocks within the clearance
   for (int i = 0; i < n; i++)
   {
      V2 p = NavCentre(g, i);
      float hF, hFWD, hC;
      BspLeaf* leaf = NULL;
      if (!BSPGetHeight(Room, &p, &hF, &hFWD, &hC, &leaf) || !leaf || !leaf->Sector)
         continue;
      if (hC - hF < OBJECTHEIGHTROO)
         continue;

      bool clear = true;
      for (int k = 0; k < NAV_RAYS && clear; k++)
      {
         V2 e;
         e.X = p.X + rayX[k];
         e.Y = p.Y + rayY[k];
         Wall* blockWall = NULL;
         clear = BSPCanMoveInRoomTree(&Room->TreeNodes[0], &p, &e, &blockWall);
      }
      if (!clear)
         continue;

      g->Walk[i] = 1;
      floors[i] = hF;
      if (leaf->Sector->Flags & NAV_SF_DEPTH)
         g->Flags[i] |= NAVF_WATER;
   }

   // links, each way checked once (E, SE, S, SW) and mirrored
   for (int r = 0; r < rows; r++)
   {
      for (int c = 0; c < cols; c++)
      {
         int i = r * cols + c;
         if (!g->Walk[i])
            continue;

         V2 a = NavCentre(g, i);
         for (int d = 0; d < 4; d++)
         {
            int nc = c + DirCol[d], nr = r + DirRow[d];
            if (nc < 0 || nr < 0 || nc >= cols || nr >= rows)
               continue;
            int j = nr * cols + nc;
            if (!g->Walk[j] || fabsf(floors[i] - floors[j]) > MAXSTEPHEIGHT)
               continue;

            V2 b = NavCentre(g, j);
            if (NavCanWalkLine(Room, &a, &b))
               g->Links[i] |= (unsigned char)(1 << d);
            if (NavCanWalkLine(Room, &b, &a))
               g->Links[j] |= (unsigned char)(1 << (d + 4));
         }
      }
   }

   // no cutting corners: a diagonal needs both sides open
   for (int i = 0; i < n; i++)
   {
      unsigned char l = g->Links[i];
      if (!l)
         continue;
      unsigned char out = l;
      for (int d = 1; d < 8; d += 2)
         if ((l & (1 << d)) && (!(l & (1 << (d - 1))) || !(l & (1 << ((d + 1) & 7)))))
            out &= (unsigned char)~(1 << d);
      g->Links[i] = out;
   }

   NavLabelAreas(g);
   free(floors);

   g->BuildMs = (int)(GetMilliCount() - start);
   return g;
}

NavGrid* NavGetGrid(room_type* Room)
{
   if (!Room)
      return NULL;
   if (!Room->Nav)
      Room->Nav = NavBuild(Room);
   return Room->Nav;
}

/*****************************************************************************************/
/* Paths                                                                                  */
/*****************************************************************************************/

typedef struct NavHeapItem
{
   float F;
   int   Cell;
} NavHeapItem;

static bool NavHeapLess(const NavHeapItem& a, const NavHeapItem& b)
{
   return a.F > b.F;   // std heap is a max-heap: invert for the lowest cost first
}

// every cell that can reach the goal learns its step toward it (Dijkstra over reversed links)
static void NavSearchField(NavGrid* g, int goal, unsigned char* next)
{
   const int n = g->Cols * g->Rows;
   float* cost = (float*)malloc(n * sizeof(float));
   for (int i = 0; i < n; i++)
      cost[i] = FLT_MAX;
   memset(next, 0xFF, n);

   std::vector<NavHeapItem> heap;
   heap.reserve(1024);
   cost[goal] = 0.0f;
   heap.push_back({ 0.0f, goal });

   while (!heap.empty())
   {
      std::pop_heap(heap.begin(), heap.end(), NavHeapLess);
      NavHeapItem top = heap.back();
      heap.pop_back();
      int i = top.Cell;
      if (top.F > cost[i])
         continue;

      // neighbours j that can step into i: j's link toward i
      for (int d = 0; d < 8; d++)
      {
         int jc = (i % g->Cols) + DirCol[d], jr = (i / g->Cols) + DirRow[d];
         if (jc < 0 || jr < 0 || jc >= g->Cols || jr >= g->Rows)
            continue;
         int j = jr * g->Cols + jc;
         int back = (d + 4) & 7;   // from j toward i
         if (!(g->Links[j] & (1 << back)))
            continue;
         float c = cost[i] + ((d & 1) ? 1.41421356f : 1.0f);
         if (c < cost[j])
         {
            cost[j] = c;
            next[j] = (unsigned char)back;
            heap.push_back({ c, j });
            std::push_heap(heap.begin(), heap.end(), NavHeapLess);
         }
      }
   }
   free(cost);
}

// the walkable cell nearest a point, within NAV_SNAP cells, in an area (0: any), that can be
// walked to from the point (or, failing that, the nearest at all when reachable is false)
static int NavNearestCell(room_type* Room, NavGrid* g, const V2* P, int area, bool reachable)
{
   int c0 = (int)floorf(P->X / NAV_CELL + 0.5f);
   int r0 = (int)floorf(P->Y / NAV_CELL + 0.5f);

   int   best[(2 * NAV_SNAP + 1) * (2 * NAV_SNAP + 1)];
   float dist[(2 * NAV_SNAP + 1) * (2 * NAV_SNAP + 1)];
   int   found = 0;

   for (int r = r0 - NAV_SNAP; r <= r0 + NAV_SNAP; r++)
      for (int c = c0 - NAV_SNAP; c <= c0 + NAV_SNAP; c++)
      {
         if (c < 0 || r < 0 || c >= g->Cols || r >= g->Rows)
            continue;
         int i = r * g->Cols + c;
         if (!g->Walk[i] || (area && g->Area[i] != area))
            continue;
         V2 q = NavCentre(g, i);
         float d = (q.X - P->X) * (q.X - P->X) + (q.Y - P->Y) * (q.Y - P->Y);

         // insertion by distance
         int k = found++;
         while (k > 0 && dist[k - 1] > d)
         {
            best[k] = best[k - 1];
            dist[k] = dist[k - 1];
            k--;
         }
         best[k] = i;
         dist[k] = d;
      }

   if (!found)
      return -1;
   if (!reachable)
      return best[0];

   for (int k = 0; k < found; k++)
   {
      V2 q = NavCentre(g, best[k]);
      if (dist[k] < 1.0f || NavCanWalkLine(Room, P, &q))
         return best[k];
   }
   return -1;
}

// whether every cell along a line is walkable: a cheap test before the real one
static bool NavGridLineClear(const NavGrid* g, const V2* a, const V2* b)
{
   const float dx = b->X - a->X, dy = b->Y - a->Y;
   int steps = (int)ceilf(sqrtf(dx * dx + dy * dy) / (NAV_CELL / 3.0f));
   for (int k = 1; k < steps; k++)
   {
      int i = NavCellAt(g, a->X + dx * (float)k / (float)steps, a->Y + dy * (float)k / (float)steps);
      if (i < 0 || !g->Walk[i])
         return false;
   }
   return true;
}

static unsigned char* NavFieldFor(NavGrid* g, int goal, bool ignoreBudget, bool* busy)
{
   UINT64 now = GetMilliCount();
   *busy = false;

   for (int k = 0; k < NAV_FIELDS; k++)
      if (g->Fields[k].Goal == goal && g->Fields[k].Next && now - g->Fields[k].Made < NAV_FIELD_MS)
         return g->Fields[k].Next;

   // a budget over time, over all rooms: there's no server tick to count in
   if (now - navWindowStart >= NAV_WINDOW_MS)
   {
      navWindowStart = now;
      navWindowSearches = 0;
   }
   if (!ignoreBudget && navWindowSearches >= NAV_SEARCHES)
   {
      *busy = true;
      return NULL;
   }
   navWindowSearches++;

   NavField* f = &g->Fields[g->FieldNext];
   g->FieldNext = (g->FieldNext + 1) % NAV_FIELDS;
   if (!f->Next)
      f->Next = (unsigned char*)NavAlloc(g->Cols * g->Rows);
   NavSearchField(g, goal, f->Next);
   f->Goal = goal;
   f->Made = now;
   return f->Next;
}

/*****************************************************************************************/
/* NavGetPathStep: the next point on the way from S to E: the farthest point along the     */
/*                 path a monster can walk to in a straight line.                         */
/*****************************************************************************************/
int NavGetPathStep(room_type* Room, const V2* S, const V2* E, V2* P, bool IgnoreBudget)
{
   NavGrid* g = NavGetGrid(Room);
   if (!g)
      return NAV_PATH_NONE;

   int from = NavNearestCell(Room, g, S, 0, true);
   if (from < 0)
      from = NavNearestCell(Room, g, S, 0, false);
   if (from < 0)
      return NAV_PATH_NONE;

   int to = NavNearestCell(Room, g, E, g->Area[from], false);
   if (to < 0)
      return NAV_PATH_NONE;

   bool busy;
   unsigned char* next = NavFieldFor(g, to, IgnoreBudget, &busy);
   if (busy)
      return NAV_PATH_BUSY;
   if (from != to && next[from] == 0xFF)
      return NAV_PATH_NONE;

   // the path's cells, from the start, as far as we may look ahead
   int path[NAV_LOOKAHEAD + 1];
   int len = 0;
   int i = from;
   path[len++] = i;
   while (i != to && len <= NAV_LOOKAHEAD)
   {
      int d = next[i];
      if (d == 0xFF)
         break;
      i += DirRow[d] * g->Cols + DirCol[d];
      path[len++] = i;
   }

   // the goal itself when the path ends there and it's in a straight line
   if (path[len - 1] == to && NavGridLineClear(g, S, E) && NavCanWalkLine(Room, S, E))
   {
      *P = *E;
      return NAV_PATH_OK;
   }

   // The first cell far enough off to be worth walking to: Kod counts a waypoint within
   // PATH_REACHED of the monster as reached, so one that near would be reached at once and
   // the monster would never leave its cell (an ant at a corner in the Flatlands)
   int first = 1;
   while (first < len - 1)
   {
      V2 q = NavCentre(g, path[first]);
      if (fabsf(q.X - S->X) > NAV_MIN_STEP || fabsf(q.Y - S->Y) > NAV_MIN_STEP)
         break;
      first++;
   }

   // pull it tight: the farthest cell on the way a straight line reaches
   for (int k = len - 1; k >= first && k > 0; k--)
   {
      V2 q = NavCentre(g, path[k]);
      if (k > first && !NavGridLineClear(g, S, &q))
         continue;
      if (k == first || NavCanWalkLine(Room, S, &q))
      {
         *P = q;
         return NAV_PATH_OK;
      }
   }

   // already on the goal's cell, with no straight way: its centre
   *P = NavCentre(g, path[0]);
   return NAV_PATH_OK;
}

/*****************************************************************************************/
/* NavWriteFile: the grid as the client's nav/<room>.nav (NAV_FORMAT 3): "NAVG", the       */
/*               version, the room's checksum, cols and rows (u16), the walkable cells (a  */
/*               bit each), each cell's links and each cell's flags (a byte each).         */
/*****************************************************************************************/
bool NavWriteFile(room_type* Room, const char* FileName)
{
   NavGrid* g = NavGetGrid(Room);
   if (!g)
      return false;

   FILE* f = fopen(FileName, "wb");
   if (!f)
      return false;

   const int n = g->Cols * g->Rows;
   const int bits = (n + 7) / 8;
   unsigned char* walk = (unsigned char*)calloc(bits, 1);
   for (int i = 0; i < n; i++)
      if (g->Walk[i])
         walk[i >> 3] |= (unsigned char)(1 << (i & 7));

   unsigned int version = NAV_FORMAT;
   unsigned int security = (unsigned int)Room->security;
   unsigned short cols = (unsigned short)g->Cols, rows = (unsigned short)g->Rows;

   bool ok = fwrite("NAVG", 1, 4, f) == 4
      && fwrite(&version, 4, 1, f) == 1
      && fwrite(&security, 4, 1, f) == 1
      && fwrite(&cols, 2, 1, f) == 1
      && fwrite(&rows, 2, 1, f) == 1
      && fwrite(walk, 1, bits, f) == (size_t)bits
      && fwrite(g->Links, 1, n, f) == (size_t)n
      && fwrite(g->Flags, 1, n, f) == (size_t)n;

   free(walk);
   fclose(f);
   return ok;
}

/*****************************************************************************************/
/* NavExportAll: the grid of every room file (PATH_ROOMS *.roo), as the file has it, as    */
/*               Dir/<room>.nav. Returns how many were written.                            */
/*****************************************************************************************/
int NavExportAll(const char* Dir)
{
#ifdef BLAK_PLATFORM_WINDOWS
   _mkdir(Dir);
#else
   mkdir(Dir, 0755);
#endif

   char spec[MAX_PATH + FILENAME_MAX];
   snprintf(spec, sizeof(spec), "%s*.roo", ConfigStr(PATH_ROOMS));
   StringVector files;
   if (!FindMatchingFiles(spec, &files))
      return 0;

   int count = 0;
   for (StringVector::iterator it = files.begin(); it != files.end(); ++it)
   {
      char roo[MAX_PATH + FILENAME_MAX];
      snprintf(roo, sizeof(roo), "%s%s", ConfigStr(PATH_ROOMS), it->c_str());

      room_type room;
      memset(&room, 0, sizeof(room));
      if (!BSPLoadRoom(roo, &room))
      {
         aprintf("Couldn't load %s\n", roo);
         continue;
      }

      // tos.roo -> <dir>/tos.nav
      char name[MAX_PATH];
      snprintf(name, sizeof(name), "%s", it->c_str());
      char* dot = strrchr(name, '.');
      if (dot)
         *dot = 0;
      for (char* c = name; *c; c++)
         *c = (char)tolower(*c);

      char path[MAX_PATH + FILENAME_MAX];
      snprintf(path, sizeof(path), "%s/%s.nav", Dir, name);
      if (NavWriteFile(&room, path))
         count++;
      else
         aprintf("Couldn't write %s\n", path);

      BSPFreeRoom(&room);
   }
   return count;
}
