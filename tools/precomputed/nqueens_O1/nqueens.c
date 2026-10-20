/* Workload: N-queens by backtracking (N = 7, 40 solutions).
   Recursion and early exits: the "is this square attacked?" loop stops at the first conflict, so its exit depends
   on the board, and the recursion depth changes the history the predictors see. A realistic mix between the
   pattern programs and sorting. */
#define N 7
static int col[N];                         /* col[r] = column of the queen in row r */

static int safe(int r, int c) {
    for (int i = 0; i < r; i++) {
        int d = col[i] - c;
        if (d == 0 || d == r - i || d == i - r) return 0;
    }
    return 1;
}

static int solve(int r) {
    if (r == N) return 1;
    int count = 0;
    for (int c = 0; c < N; c++)
        if (safe(r, c)) { col[r] = c; count += solve(r + 1); }
    return count;
}

int main(void) {
    return solve(0);                       /* 40 */
}
