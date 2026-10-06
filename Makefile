fmt:
	ruff format .
	git ls-files '*.cpp' '*.h' | xargs clang-format -i
	bunx prettier -u -w .
