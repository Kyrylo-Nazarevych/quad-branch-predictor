/* Workload: recursive quicksort (Lomuto partition) of 300 pseudo-random numbers, then a check that they are sorted.
   Compare with bubble sort in sort_and_search: the partition compare (line 17) is just as data-dependent (about
   50/50, the hard branch), but quicksort runs it far fewer times, and the recursion adds its own branches. */
#define N 300
static int data[N];
static unsigned int state = 521288629u;

static unsigned int rnd(void) {            /* xorshift32 pseudo-random numbers */
    state ^= state << 13; state ^= state >> 17; state ^= state << 5;
    return state;
}

static void quicksort(int *a, int lo, int hi) {
    while (lo < hi) {
        int pivot = a[hi], p = lo;
        for (int i = lo; i < hi; i++) {
            if (a[i] < pivot) { int t = a[i]; a[i] = a[p]; a[p] = t; p++; }
        }
        int t = a[p]; a[p] = a[hi]; a[hi] = t;
        if (p - lo < hi - p) { quicksort(a, lo, p - 1); lo = p + 1; }   /* recurse into the smaller side */
        else                 { quicksort(a, p + 1, hi); hi = p - 1; }
    }
}

int main(void) {
    for (int i = 0; i < N; i++) data[i] = rnd() % 1000;
    quicksort(data, 0, N - 1);
    for (int i = 1; i < N; i++)
        if (data[i - 1] > data[i]) return 99;   /* not sorted: should never happen */
    return data[N / 2] & 0x7f;
}
