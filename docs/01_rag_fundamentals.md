# 01 — RAG Fundamentals

*Read this first. It assumes no prior knowledge of RAG, embeddings, or vector databases.*

---

## The problem RAG solves

A language model knows what it learned during training. It does **not** know:

- what is in your lecture notes,
- what your company's internal policy says,
- what happened last week,
- anything at all about a document you just uploaded.

Ask it anyway and something worse than "I don't know" happens. It **invents** an
answer that sounds authoritative. This is a **hallucination**, and it is not a bug
that will be patched away — it is the model doing exactly what it was trained to do:
produce plausible text.

There are only three ways to fix this:

| Approach | What it means | Why we did not pick it |
| --- | --- | --- |
| **Fine-tuning** | Retrain the model on your documents | Expensive, slow, needs ML expertise, and the model still cannot cite a source |
| **Long context** | Paste every document into every prompt | Costly per question, and models lose accuracy in the middle of very long inputs |
| **RAG** | Retrieve only the relevant parts, then ask | **This one** |

---

## What RAG is

**Retrieval-Augmented Generation.** Two words, two halves:

- **Retrieval** — find the passages in your documents that are relevant to the question.
- **Augmented Generation** — give those passages to the language model and ask it to
  answer *using them*.

The mental model that makes it click:

> The language model is a **brilliant writer who has never read your documents**.
> RAG is the research assistant who finds the right pages and puts them on the desk
> before the writer starts typing.

The writer still does the writing. But now the writing is based on pages that exist,
not on memory.

---

## The five words you need

**Chunk**
A small piece of a document — roughly a paragraph or two. Documents are split into
chunks because retrieval works on small units. You cannot hand a 300-page PDF to a
search function and get a useful result; you hand it 1,200 chunks and ask which three
are relevant.

**Embedding**
A list of numbers that represents the *meaning* of a piece of text. Typically a few
hundred numbers. Two texts about the same subject produce similar numbers even if
they share no words. This is what makes search work on meaning rather than on spelling.

**Vector**
Just another word for that list of numbers. "Vector database" means "database that
stores and searches lists of numbers".

**Cosine similarity**
How close two vectors are in direction, from −1 to 1. Close to 1 means "these mean
similar things". This is the scoring function behind retrieval.

**Grounding**
Checking that the generated answer is actually supported by the retrieved passages.
A grounded system can say "the documents do not cover this". An ungrounded one
invents something.

---

## The pipeline, end to end

There are two halves. They are **deliberately separate** and meet in exactly one
place — the vector store. This separation is the single most important design decision
in the project; [02 — Architecture](02_architecture.md) explains why.

```
INGESTION  (runs once per document, can take minutes)

  Upload → Validate → Parse → Extract → Normalise → Chunk → Embed → Store → Ready


QUERY  (runs once per question, must take seconds)

  Question → Analyse → Embed → Vector Search → Retrieve → [Re-rank]
           → Build Context → Generate → Ground → Resolve Citations → Answer

                                    ▲
                                    │
                    the two halves meet ONLY here
                         (the vector store)
```

---

## What each stage does, in one line

### Ingestion

| Stage | One-line job |
| --- | --- |
| **Upload** | Accept the file, check its size and type |
| **Validate** | Confirm it really is what its extension claims |
| **Parse** | Turn the bytes into text, keeping structure (pages, slides, sheets) |
| **Extract** | Pull out the meaningful text, tables and headings |
| **Normalise** | Convert every format into ONE common internal shape |
| **Chunk** | Split into overlapping pieces of a sensible size |
| **Embed** | Convert each chunk into a vector |
| **Store** | Save the vectors, and save the chunks in the database |
| **Ready** | Mark the document as searchable |

### Query

| Stage | One-line job |
| --- | --- |
| **Question** | Accept the user's question |
| **Analyse** | Understand what is being asked (and whether it is answerable at all) |
| **Embed** | Convert the question into a vector, the same way chunks were |
| **Vector Search** | Ask the vector store for the nearest chunks |
| **Retrieve** | Fetch the full chunk records from the database |
| **[Re-rank]** | Optionally reorder the candidates (see [06](06_advanced_rag.md)) |
| **Build Context** | Assemble the chosen chunks into a prompt |
| **Generate** | Ask the language model to answer from that context |
| **Ground** | Verify the answer is supported by the evidence |
| **Resolve Citations** | Map `[1]`, `[2]` back to real documents and pages |
| **Answer** | Return the answer, its evidence, and its grounding verdict |

---

## Why the order matters

Some of these stages cannot be moved. Understanding *why* is most of understanding RAG.

- **Chunking must come before embedding.** You embed chunks, not documents. Embed a
  whole document and you get one vector averaging every topic in it — useless for
  finding a specific fact.
- **The same embedding model must be used for ingestion and query.** A question and a
  chunk are only comparable if they were converted by the same model. Mixing models is
  the single most common beginner mistake: retrieval silently returns nonsense, with no
  error message. See [04](04_embeddings_and_vector_store.md).
- **Generation must come after retrieval.** Obviously — but note that the model is the
  *last* stage, not the first. RAG is a retrieval system that uses a language model,
  not a language model that occasionally searches.
- **Grounding must come after generation.** You cannot check an answer that does not
  exist yet.
- **Citations must come after grounding.** A citation is a claim that "this evidence
  supports that sentence". You need both parts before you can check the claim.

---

## What RAG cannot do

Being honest about limits is part of understanding the technique.

**RAG cannot fix retrieval failures.** If the right chunk is not retrieved, no
downstream stage can recover it. Re-ranking cannot. A better prompt cannot. This is why
retrieval quality (Recall@K — see [14](14_evaluation.md)) is the metric that matters
most.

**RAG does not make the model trustworthy.** It makes the model's *inputs* verifiable.
The model can still misread a passage, or combine two passages into a claim neither
supports. This is why Carinaa has a separate grounding stage that reports
`PARTIALLY_SUPPORTED` rather than pretending everything is fine.

**RAG cannot eliminate prompt injection.** If a document contains the text "ignore your
instructions", that text is now inside your prompt. Carinaa mitigates this by treating
documents as untrusted data in a delimited block, and by never putting a secret in the
prompt — but mitigation is not elimination. See [10](10_security_and_isolation.md).

**RAG is only as good as the documents.** Garbage in, confidently-cited garbage out.

---

## Next

→ [02 — Architecture](02_architecture.md): the two pipelines in detail, and the
reason they only meet at the vector store.
