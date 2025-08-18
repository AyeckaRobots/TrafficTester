#!/usr/bin/env python3
import csv

class DBManager:
    def __init__(self, input_csv='sweep_results.csv', output_csv='sweep_results.csv'):
        self.input_csv = input_csv
        self.output_csv = output_csv

    def sort_and_dedup(self):
        # Read all rows from the CSV
        with open(self.input_csv, newline='') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        # Sort by numeric values of the first four key fields
        rows.sort(key=lambda r: (
            float(r['frequency_mhz']),
            float(r['symbol_rate_msps']),
            float(r['power_dbm']),
            int(r['noise_dec'])
        ))

        # Deduplicate
        seen_exact = set()
        seen_noise_agnostic = set()
        unique_rows = []

        for row in rows:
            # Exact duplicate key (old behavior)
            exact_key = (
                row['frequency_mhz'],
                row['symbol_rate_msps'],
                row['power_dbm'],
                row['noise_dec']
            )
            # Noise-agnostic key: ignores noise_dec, matches esno_db
            noise_agnostic_key = (
                row['frequency_mhz'],
                row['symbol_rate_msps'],
                row['power_dbm'],
                row['esno_db']
            )

            if exact_key in seen_exact or noise_agnostic_key in seen_noise_agnostic:
                continue  # skip this row

            # Otherwise keep it and mark as seen
            seen_exact.add(exact_key)
            seen_noise_agnostic.add(noise_agnostic_key)
            unique_rows.append(row)

        # Write cleaned, sorted data
        with open(self.output_csv, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(unique_rows)

if __name__ == "__main__":
    manager = DBManager()
    manager.sort_and_dedup()
    print(f"Sorted and deduplicated data written to '{manager.output_csv}'")
