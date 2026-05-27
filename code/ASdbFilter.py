import pandas as pd

# Load your actual CSV file
# https://asdb.stanford.edu/ download, we used 2026-03_categorized_ases.csv
df = pd.read_csv('INPUT ASDB CSV', engine='python')

targets = [
    "Education and Research",
    "Elementary and Secondary Schools",
    "Colleges, Universities, and Professional Schools",
    "Other Schools, Instruction, and Exam Preparation (Trade Schools, Art Schools, Driving Instruction, etc.)"
]

# df.isin(targets) creates a True/False table of the exact same shape as your data.
# .any(axis=1) checks horizontally across the rows to see if at least one 'True' exists.
mask = df.isin(targets).any(axis=1)

# Apply the mask to filter the original dataframe
filtered_df = df[mask]

# Output the result to a new CSV file
filtered_df.to_csv('./data/filtered_asdb.csv', index=False)

print(f"Filtering complete. Found {len(filtered_df)} matching rows.")
print("Output saved to 'filtered_asdb.csv'")
