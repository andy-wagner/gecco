#========================================================================
#GECCO - Generic Enviroment for Context-Aware Correction of Orthography
# Maarten van Gompel, Wessel Stoop, Antal van den Bosch
# Centre for Language and Speech Technology
# Radboud University Nijmegen
#
# Sponsored by Revisely (http://revise.ly)
#
# Licensed under the GNU Public License v3
#
#=======================================================================

import sys
import os
import json
import io
import bz2
import gzip
from pynlpl.formats import folia
from pynlpl.textprocessors import Windower
from timbl import TimblClassifier
from gecco.gecco import Module
from gecco.helpers.hapaxing import gethapaxer


class TIMBLPuncRecaseModule(Module):
    UNIT = folia.Word

    def verifysettings(self):
        if 'class' not in self.settings:
            self.settings['class'] = 'confusible'

        super().verifysettings()

        if 'algorithm' not in self.settings:
            self.settings['algorithm'] = 1

        if 'leftcontext' not in self.settings:
            self.settings['leftcontext'] = 3

        if 'rightcontext' not in self.settings:
            self.settings['rightcontext'] = 3

        self.hapaxer = gethapaxer(self.settings)


        try:
            modelfile = self.models[0]
            if not modelfile.endswith(".ibase"):
                raise Exception("TIMBL models must have the extension ibase, got " + modelfile + " instead")
        except:
            raise Exception("Expected one model, got 0 or more")

    def gettimbloptions(self):
        return "-F Tabbed " + "-a " + str(self.settings['algorithm']) + " +D +vdb -G0"

    def load(self):
        """Load the requested modules from self.models"""
        if self.hapaxer:
            self.log("Loading hapaxer...")
            self.hapaxer.load()

        if not self.models:
            raise Exception("Specify one or more models to load!")

        self.log("Loading models...")
        modelfile = self.models[0]
        if not os.path.exists(modelfile):
            raise IOError("Missing expected model file: " + modelfile + ". Did you forget to train the system?")
        self.log("Loading model file " + modelfile + "...")
        fileprefix = modelfile.replace(".ibase","") #has been verified earlier
        self.classifier = TimblClassifier(fileprefix, self.gettimbloptions())
        self.classifier.load()


    def addtraininstance(self,classifier, buffer,l,r):
        """Helper function"""
        focusword, cased, punc = buffer[l+1]
        cls = punc
        if cased:
            cls += 'C'
        if not cls:
            cls = '-'
        if self.hapaxer:
            features = [w for w,_,_ in buffer]
            features = [w.lower() for w in  self.hapaxer(features[:l]) + (features[l+1],) + self.hapaxer(features[l+2:])]
        else:
            features = [w.lower() for w,_,_ in buffer]
        classifier.append( tuple(features) , cls )
        return buffer[1:]

    def train(self, sourcefile, modelfile, **parameters):
        if self.hapaxer:
             self.log("Training hapaxer...")
             self.hapaxer.train()

        l = self.settings['leftcontext']
        r = self.settings['rightcontext']
        n = l + 1 + r

        self.log("Generating training instances...")
        fileprefix = modelfile.replace(".ibase","") #has been verified earlier
        classifier = TimblClassifier(fileprefix, self.gettimbloptions())
        if sourcefile.endswith(".bz2"):
            iomodule = bz2
        elif sourcefile.endswith(".gz"):
            iomodule = gzip
        else:
            iomodule = io

        buffer = [("<begin>",False,'')] * l
        with iomodule.open(sourcefile,mode='rt',encoding='utf-8') as f:
            for line in f:
                words = line.split(' ')
                for i, word in enumerate(words):
                    if i == 0 or word[i-1].isalnum():
                        punc = ''
                    else:
                        punc = word[i-1]
                    buffer.append( (word, word == word[0].upper() + word[1:].lower(), punc ) )
                    if len(buffer) == l + r + 1:
                        buffer = self.addtraininstance(classifier, buffer,l,r)
                for i in range(0,r):
                    buffer.append( ("<end>",False,'') )
                    if len(buffer) == l + r + 1:
                        buffer = self.addtraininstance(classifier, buffer,l,r)

        self.log("Training classifier...")
        classifier.train()

        self.log("Saving model " + modelfile)
        classifier.save()


    def classify(self, word):
        features = self.getfeatures(word)
        if self.hapaxer: features = self.hapaxer(features)
        best, distribution,_ = self.classifier.classify(features)
        return best, distribution


    def getfeatures(self, word):
        """Get features at testing time, crosses sentence boundaries"""
        l = self.settings['leftcontext']
        r = self.settings['rightcontext']

        leftcontext = []
        currentword = word
        while len(leftcontext) < l:
            prevword = currentword.prev(folia.Word,None)
            if prevword:
                w = prevword.text.lower()
                if w.isalnum():
                    leftcontext.insert(0, w )
            else:
                leftcontext.insert(0, "<begin>")

        rightcontext = []
        currentword = word
        while len(rightcontext) < r:
            nextword = currentword.next(folia.Word,None)
            if nextword:
                w = nextword.text.lower()
                if w.isalnum():
                    rightcontext.append(w )
            else:
                rightcontext.append("<end>")

        return leftcontext + [word.text().lower()] + rightcontext

    def processresult(self, word, lock, cls, distribution):
        #TODO: process result
        #self.addsuggestions(lock, word, list(distribution.items()))
        raise NotImplementedError

    def run(self, word, lock, **parameters):
        """This method gets invoked by the Corrector when it runs locally. word is a folia.Word instance"""
        wordstr = str(word)
        if wordstr.isalnum():
            cls, distribution = self.classify(word)
            processresult(word,lock,cls,distribution)

    def runclient(self, client, word, lock, **parameters):
        """This method gets invoked by the Corrector when it should connect to a remote server, the client instance is passed and already available (will connect on first communication). word is a folia.Word instance"""
        wordstr = str(word)
        if wordstr.isalnum():
            cls, distribution = json.loads(client.communicate(json.dumps(self.getfeatures(word))))
            processresult(word,lock,cls,distribution)

    def server_handler(self, features):
        """This method gets called by the module's server and handles a message by the client. The return value (str) is returned to the client"""
        features = tuple(json.loads(features))
        best,distribution,_ = self.classifier.classify(features)
        return json.dumps([best,distribution])
