// Meridian 59, Copyright 1994-2012 Andrew Kirmse and Chris Kirmse.
// All rights reserved.
//
// This software is distributed under a license that is described in
// the LICENSE file that accompanies it.
//
// Meridian is a registered trademark.
/*
 * navgrid.h
 *
 * Ours (ADR 0029): where a monster can walk in a room, as a grid of half squares built from
 * the server's own move rule, and the paths over it. The same grid is exported as the
 * browser client's nav/<room>.nav files.
 */

#ifndef _NAVGRID_H
#define _NAVGRID_H

#define NAV_CELL          512.0f   // a cell is half a square (ROO fineness)
#define NAV_CLEARANCE     280.0f   // a cell's centre keeps this far from a wall that blocks
#define NAV_FORMAT        3        // the .nav file layout, as the client's NAV_FORMAT
#define NAV_FIELDS        4        // paths kept per room, by goal cell
#define NAV_FIELD_MS      1000     // how long a kept path stays good
#define NAV_WINDOW_MS     100      // the search budget's window
#define NAV_SEARCHES      8        // searches allowed in a window, over all rooms

// cell flags
#define NAVF_WATER        0x01     // the sector has depth: monsters wade, the client keeps out

// results of NavGetPathStep
#define NAV_PATH_OK       0        // P holds the next point to walk to
#define NAV_PATH_NONE     1        // no way there
#define NAV_PATH_BUSY     2        // over this window's budget: take the straight step for now

typedef struct NavField
{
   int            Goal;            // the goal cell, -1 when unused
   UINT64         Made;            // GetMilliCount when it was searched
   unsigned char* Next;            // per cell: the direction one step nearer the goal, 0xFF none
} NavField;

typedef struct NavGrid
{
   int             Cols;
   int             Rows;
   unsigned char*  Walk;           // 1 where a monster can stand
   unsigned char*  Links;          // bit d: the way to neighbour d is open (E, SE, S, SW, W, NW, N, NE)
   unsigned char*  Flags;          // NAVF_*
   unsigned short* Area;           // connected area (two-way links), largest first; 0 for none
   int             BuildMs;
   NavField        Fields[NAV_FIELDS];
   int             FieldNext;
} NavGrid;

NavGrid* NavGetGrid(room_type* Room);
void     NavFreeGrid(room_type* Room);
bool     NavCanWalkLine(room_type* Room, const V2* S, const V2* E);
int      NavGetPathStep(room_type* Room, const V2* S, const V2* E, V2* P, bool IgnoreBudget);
bool     NavWriteFile(room_type* Room, const char* FileName);
int      NavExportAll(const char* Dir);

#endif
