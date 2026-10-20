/* Workload: 12 x 12 integer matrix multiply, three times.
   Only counted loops with fixed trip counts: the most regular code there is. Every predictor should be near 100%,
   missing little more than the loop exits (inner loop of 12, like pattern_loop12). Compare with sort_and_search,
   whose data-dependent compare is what makes it hard. */
#define N 12
static int a[N][N], b[N][N], c[N][N];

static void multiply(void) {
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            int s = 0;
            for (int k = 0; k < N; k++) s += a[i][k] * b[k][j];
            c[i][j] = s;
        }
}

int main(void) {
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) { a[i][j] = i + j; b[i][j] = i - j; }
    int check = 0;
    for (int rep = 0; rep < 3; rep++) {
        multiply();
        check += c[rep][N - 1 - rep];
    }
    return check & 0x7f;
}
