/* Example workload: no printf, no OS. Result is returned from main (exit code). */
#define N 200
static int data[N];
static unsigned int seed = 12345;
static unsigned int rnd(void) { seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5; return seed; }

static void bubble_sort(int *a, int n) {
    for (int i = 0; i < n - 1; i++) {
        int swapped = 0;
        for (int j = 0; j < n - 1 - i; j++)
            if (a[j] > a[j + 1]) { int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t; swapped = 1; }
        if (!swapped) break;
    }
}
static int binary_search(const int *a, int n, int key) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (a[mid] == key) return mid;
        if (a[mid] < key) lo = mid + 1; else hi = mid - 1;
    }
    return -1;
}
int main(void) {
    for (int i = 0; i < N; i++) data[i] = rnd() % 1000;
    bubble_sort(data, N);
    int found = 0;
    for (int q = 0; q < 300; q++) if (binary_search(data, N, rnd() % 1000) >= 0) found++;
    int evens = 0;
    for (int i = 0; i < N; i++) if ((data[i] & 1) == 0) evens++;
    return (found + evens) & 0x7f;
}
