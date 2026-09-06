# Virtual Memory

Operating systems use virtual addresses to isolate processes from physical memory.

## Page Tables

Page tables map virtual pages to physical frames.

## Translation Lookaside Buffer

The TLB is a CPU cache of recently used address translations.

## TLB Shootdowns

When page tables change, one CPU may request other CPUs invalidate stale translations.
