/* Pattern: an inner loop of 4 iterations. Its exit branch repeats T,T,T,N.
   Bimodal misses every exit; history-based predictors learn the exit. */
volatile int sink;

int main(void) {
    for (int i = 0; i < 1000; i++)
        for (int j = 0; j < 4; j++)
            sink = i + j;
    return 0;
}
