import re


def search_context(sentences, synonyms):

    results = []

    # Normalisasi keyword
    keywords = []

    for keyword in synonyms["symbolic"] + synonyms["textual"]:
        keyword = keyword.strip()

        # Skip keyword kosong
        if not keyword:
            continue

        # Skip symbolic yang terlalu pendek (< 3 karakter)
        # karena mudah menghasilkan false positive
        if len(keyword) < 3:
            continue

        keywords.append(keyword)

    # Cari keyword pada setiap sentence
    for sentence in sentences:

        for keyword in keywords:

            pattern = r"\b" + re.escape(keyword) + r"\b"

            if re.search(pattern, sentence, re.IGNORECASE):

                results.append({
                    "keyword": keyword,
                    "sentence": sentence
                })

                break

    return results
