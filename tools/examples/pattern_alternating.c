/* Pattern: one branch that alternates taken / not taken.
   Bimodal cannot learn it; any predictor with global history can. */
volatile int sink_a, sink_b;

int main(void) {
    for (int i = 0; i < 2000; i++) {
        if (i & 1) sink_a = i;      /* T, N, T, N, ... */
        else       sink_b = i;
    }
    return 0;
}
