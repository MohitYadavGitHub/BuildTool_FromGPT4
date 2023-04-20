

# GPT 4 wrote this Build tool

This is a barebones build tool. Purpose of this tool is to evaluate my understanding and GPT4s understanding of what build tools are.

All the code was written by GPT4. That was the main target. Took about 50 prompts.

This is still very much WIP. Target will remain to write zero code, and let GPT4 write everything.

The tool probably still has a lot of bugs, and the code is not optimal. Goal there is also to make GPT4 refactor after every working version.

## How to use
Specification of Build Targets are done in .bld files. Format is yaml and spec should be intutive to anyone who has used build tools.
Ex: 
```
link:
  name: "link"
  command: "cat folder1/test.txt>test2.txt "
  outputs: 
    - "test.txt"
  dependencies:
    - compile
```
```
python BuildTool.py build link
```

## Features
* Can scan all the Bld (build specification) files in the repo.
* Can cache Outputs
* Understands dependencies between tasks
* Cached files are stored in a filestore, no duplicates for same file are stored in cache
* Only tasks for which their inputs or command to run the task changes are rerun. All dependent tasks will be rerun as well.

## Future Features
* Remote cache
* Configuration options , for ex: to have a a local or remtoe cache folder
* ...

## Currrent API spport

```
python BuildTool.py build <target-name>
python BuildTool.py clean
```

## Installed requirements

You'll need python , and the followin modules in a virtual environment
* networkx
* pyyaml

networkx is a heavy module, so will ask GPT4 to write a graph Lib for the minimum things that are needed to repalce it in the future.
