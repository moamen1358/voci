package co.voci.util

/**
 * Paginated text buffer. After every `wordsPerPage` committed words, the previous
 * page is dropped and the next word starts a fresh page. Mirrors the Python
 * `_paginate` logic in voci/main.py.
 */
class PaginatedLine(private val wordsPerPage: Int = 10) {

    private val committedWords = mutableListOf<String>()

    @Synchronized
    fun commit(text: String) {
        text.trim().split("\\s+".toRegex())
            .filter { it.isNotBlank() }
            .forEach { committedWords.add(it) }
    }

    @Synchronized
    fun committedDisplay(): String = page(committedWords)

    @Synchronized
    fun withPartial(partial: String): String {
        val partialWords = partial.trim().split("\\s+".toRegex()).filter { it.isNotBlank() }
        val combined = committedWords + partialWords
        return page(combined)
    }

    @Synchronized
    fun clear() { committedWords.clear() }

    private fun page(words: List<String>): String {
        if (words.isEmpty()) return ""
        val pageStart = (words.size - 1) / wordsPerPage * wordsPerPage
        return words.subList(pageStart, words.size).joinToString(" ")
    }
}
