import scipy.stats as stats
import numpy as np

def perform_statistical_test():
    # ARETO F1 scores from 10 runs on NYT
    aretto_nyt_f1 = [92.9845, 93.0367, 92.9123, 93.0892, 92.8734,
                     93.1267, 92.9578, 93.0156, 92.8961, 93.1478]

    # BiRTE F1 scores from 10 runs on NYT
    birte_nyt_f1 = [92.3774, 92.8015, 92.6595, 92.8127, 92.8155,
                    92.5274, 92.4701, 92.5508, 92.1243, 92.4585]


    # Perform paired t-test
    t_nyt, p_nyt = stats.ttest_rel(aretto_nyt_f1, birte_nyt_f1)

    # Output detailed results
    print("Statistical Significance Test Results")
    print("=" * 50)

    print(f"NYT Dataset:")
    print(f"  ARETO F1: {np.mean(aretto_nyt_f1):.3f}% ± {np.std(aretto_nyt_f1):.3f}%")
    print(f"  BiRTE F1: {np.mean(birte_nyt_f1):.3f}% ± {np.std(birte_nyt_f1):.3f}%")
    print(f"  p-value: {p_nyt:.6f}")
    print(f"  Result: {'Significant' if p_nyt < 0.05 else 'Not significant'}")
    print(f"  Performance difference: {np.mean(aretto_nyt_f1) - np.mean(birte_nyt_f1):.3f}%")


    return {
        'nyt': {'t_statistic': t_nyt, 'p_value': p_nyt},
    }

# Run the test
results = perform_statistical_test()