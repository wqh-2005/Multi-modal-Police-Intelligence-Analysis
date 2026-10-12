# ARETO: A Joint Entity and Relation Extraction Model for the Triple Overlapping Problem

SEO_R is a newly established test set specifically designed for [WebNLG_star]. We have retained 
the WebNLG_star dataset here, while the other two datasets (including the large-scale NYT dataset) should be obtained 
from their official websites according to the reference documents in the paper.

Official implementation of "ARETO: A Joint Entity and Relation Extraction Model for the Triple Overlapping Problem"

## Quick Start
For the configured Windows environment, local BERT paths, and training commands,
see [README.local.md](README.local.md). The original README is saved in README.upstream.md.

The main execution script is `run.py`.

```bash
# Install dependencies
pip install -r requirements-reproduction.txt

# Train the model
python run.py --dataset WebNLG_star --batch_size 2
```
