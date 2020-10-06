// MIT License
//
// Copyright (c) 2020 Tweag IO
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
//

package log

import (
	"fmt"
	"github.com/tweag/trustix/storage"
)

type logStorage struct {
	txn storage.Transaction
}

func (s *logStorage) LevelSize(level int) int {
	n, err := s.txn.Size([]byte(fmt.Sprintf("log-%d")))
	if err != nil {
		if err == storage.ObjectNotFoundError {
			return 1
		}
		panic(err)
	}

	return n
}

func (s *logStorage) Size() int {
	n, err := s.txn.Size([]byte("log-root"))
	if err != nil {
		if err == storage.ObjectNotFoundError {
			return 1
		}
		panic(err)
	}
	return n
}

func (s *logStorage) Get(level int, idx int) *Leaf {
	bucket := []byte(fmt.Sprintf("log-%d", level))
	key := []byte(fmt.Sprintf("%d", idx))

	v, err := s.txn.Get(bucket, key)
	if err != nil {
		panic(err)
	}

	l, err := LeafFromBytes(v)
	if err != nil {
		panic(err)
	}

	return l
}

func (s *logStorage) Append(level int, leaf *Leaf) {
	if s.Size() == level {
		// "Grow" root level by adding another "level"
		err := s.txn.Set([]byte("log-root"), []byte(fmt.Sprintf("%d", s.Size())), []byte(""))
		if err != nil {
			panic(err)
		}
	}

	v, err := leaf.Marshal()
	if err != nil {
		panic(err)
	}

	idx := s.LevelSize(level) - 1

	bucket := []byte(fmt.Sprintf("log-%d", level))
	key := []byte(fmt.Sprintf("%d", idx))

	err = s.txn.Set(bucket, key, v)
	if err != nil {
		panic(err)
	}

}