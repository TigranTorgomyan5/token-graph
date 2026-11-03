package ledger

var entries []string

func Record(entry string) {
	entries = append(entries, entry)
}
